# %%

import os; os.environ["ACCELERATE_DISABLE_RICH"] = "1"
import sys
import torch as t
from torch import nn, Tensor
from torch.nn import functional as F
from dataclasses import dataclass
import time
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import einops
import plotly.express as px
from pathlib import Path
from jaxtyping import Float
from typing import Optional
from tqdm.auto import tqdm
from dataclasses import dataclass

# Make sure exercises are in the path
chapter = r"chapter1_transformers"
exercises_dir = Path(f"{os.getcwd().split(chapter)[0]}/{chapter}/exercises").resolve()
section_dir = exercises_dir / "part7_toy_models_of_superposition"
if str(exercises_dir) not in sys.path: sys.path.append(str(exercises_dir))

from plotly_utils import imshow, line

from part7_toy_models_of_superposition.utils import plot_W, plot_Ws_from_model, render_features
import part7_toy_models_of_superposition.tests as tests
# import part7_toy_models_of_superposition.solutions as solutions

device = t.device("cuda" if t.cuda.is_available() else "cpu")

MAIN = __name__ == "__main__"

# %%


if MAIN:
	W = t.randn(2, 5)
	W_normed = W / W.norm(dim=0, keepdim=True)
	
	imshow(W_normed.T @ W_normed, title="Cosine similarities of each pair of 2D feature embeddings", width=600)

# %%


if MAIN:
	plot_W(W_normed)

# %%

@dataclass
class Config:
	n_instances: int
	n_features: int = 5
	n_hidden: int = 2


class Model(nn.Module):
	W: Float[Tensor, "n_instances n_hidden n_features"]
	b_final: Float[Tensor, "n_instances n_features"]
	# Our linear map (ignoring n_instances) is x -> ReLU(W.T @ W @ x + b_final)

	def __init__(
		self, 
		config: Config, 
		feature_probability: Optional[Tensor] = None,
		importance: Optional[Tensor] = None,               
		device=device
	):
		super().__init__()
		self.config = config

		if feature_probability is None: feature_probability = t.ones(())
		self.feature_probability = feature_probability.to(device)
		if importance is None: importance = t.ones(())
		self.importance = importance.to(device)
		
		self.W = nn.Parameter(t.empty((config.n_instances, config.n_hidden, config.n_features), device=device))
		nn.init.xavier_normal_(self.W)
		self.b_final = nn.Parameter(t.zeros((config.n_instances, config.n_features), device=device))


	
	def forward(
		self, 
		features: Float[Tensor, "... instances features"]
	) -> Float[Tensor, "... instances features"]:
		hidden = einops.einsum(
		features, self.W,
		"... instances features, instances hidden features -> ... instances hidden"
		)
		out = einops.einsum(
			hidden, self.W,
			"... instances hidden, instances hidden features -> ... instances features"
		)
		out = out + self.b_final
		out = F.relu(out)
		return out


	def generate_batch(self, n_batch):
		'''
		Generates a batch of data
		'''
		feat = t.rand((n_batch, self.config.n_instances, self.config.n_features), device=self.W.device)
		batch = t.where(
			t.rand((n_batch, self.config.n_instances, self.config.n_features), device=self.W.device) <= self.feature_probability,
			feat,
			t.zeros((), device=self.W.device),
		)
		return batch



if MAIN:
	tests.test_model(Model)

# %%

def linear_lr(step, steps):
	return (1 - (step / steps))
	
def constant_lr(*_):
	return 1.0
	
def cosine_decay_lr(step, steps):
	return np.cos(0.5 * np.pi * step / (steps - 1))
	
def optimize(
	model, 
	n_batch=1024,
	steps=10_000,
	print_freq=100,
	lr=1e-3,
	lr_scale=constant_lr,
	hooks=[]
):
	cfg = model.config

	opt = t.optim.AdamW(list(model.parameters()), lr=lr)

	start = time.time()
	progress_bar = tqdm(range(steps))
	for step in progress_bar:
		step_lr = lr * lr_scale(step, steps)
		for group in opt.param_groups:
			group['lr'] = step_lr
			opt.zero_grad(set_to_none=True)
			batch = model.generate_batch(n_batch)
			out = model(batch)
			error = (model.importance*(batch.abs() - out)**2)
			loss = einops.reduce(error, 'b i f -> i', 'mean').sum()
			loss.backward()
			opt.step()

			if hooks:
				hook_data = dict(model=model, step=step, opt=opt, error=error, loss=loss, lr=step_lr)
				for h in hooks: h(hook_data)
			if step % print_freq == 0 or (step + 1 == steps):
				progress_bar.set_postfix(
					loss=loss.item() / cfg.n_instances,
					lr=step_lr,
				)

# %%


if MAIN:
	config = Config(
		n_instances = 10,
		n_features = 5,
		n_hidden = 2,
	)
	
	importance = (0.9**t.arange(config.n_features))

	feature_probability = (20 ** -t.linspace(0, 1, config.n_instances))
	
	line(importance, width=600, height=400, title="Importance of each feature (same over all instances)", labels={"y": "Feature importance", "x": "Feature"})

	line(feature_probability, width=600, height=400, title="Feature probability (varied over instances)", labels={"y": "Probability", "x": "Instance"})

# %%


if MAIN:
	model = Model(
		config=config,
		device=device,
		importance=importance[None, :],
		feature_probability=feature_probability[:, None]
	)


if MAIN:
	optimize(model)

# %%


if MAIN:
	plot_Ws_from_model(model, config)

# %% VISUALIZING FEATURES ACROSS VARYING SPARSITY


if MAIN:
	config = Config(
		n_instances = 20,
		n_features = 100,
		n_hidden = 20,
	)
	
	importance = (100 ** -t.linspace(0, 1, config.n_features))

	feature_probability = (20 ** -t.linspace(0, 1, config.n_instances))
	
	line(importance, width=600, height=400, title="Importance of each feature (same over all instances)", labels={"y": "Feature importance", "x": "Feature"})

	line(feature_probability, width=600, height=400, title="Feature probability (varied over instances)", labels={"y": "Probability", "x": "Instance"})

# %%


if MAIN:
	model = Model(
		config=config,
		device=device,
		importance = importance[None, :],
		feature_probability = feature_probability[:, None]
	)


if MAIN:
	optimize(model)

# %%


if MAIN:
	fig = render_features(model, np.s_[::2])
	fig.update_layout(width=1200, height=2000)

# %%


if MAIN:
	config = Config(
		n_features = 200,
		n_hidden = 20,
		n_instances = 20,
	)
	
	feature_probability = (20 ** -t.linspace(0, 1, config.n_instances))
	
	model = Model(
		config=config,
		device=device,
		# For this experiment, use constant importance.
		feature_probability = feature_probability[:, None]
	)


if MAIN:
	optimize(model)

# %%


if MAIN:
	fig = px.line(
		x=1/model.feature_probability[:, 0].cpu(),
		y=(model.config.n_hidden/(t.linalg.matrix_norm(model.W.detach(), 'fro')**2)).cpu(),
		log_x=True,
		markers=True,
		template="ggplot2",
		height=600,
		width=1000,
		title=""
	)
	fig.update_xaxes(title="1/(1-S), <-- dense | sparse -->")
	fig.update_yaxes(title=f"m/||W||_F^2")

# %%

@t.no_grad()
def compute_dimensionality(W):
	norms = t.linalg.norm(W, 2, dim=-1) 
	W_unit = W / t.clamp(norms[:, :, None], 1e-6, float('inf'))

	interferences = (t.einsum('eah,ebh->eab', W_unit, W)**2).sum(-1)

	dim_fracs = (norms**2/interferences)
	return dim_fracs.cpu()



if MAIN:
	dim_fracs = compute_dimensionality(model.W.transpose(-1, -2))
	
	
	density = model.feature_probability[:, 0].cpu()
	W = model.W.detach()
	
	for a,b in [(1,2), (2,3), (2,5), (2,6), (2,7)]:
		val = a/b
		fig.add_hline(val, line_color="purple", opacity=0.2, annotation=dict(text=f"{a}/{b}"))
	
	for a,b in [(5,6), (4,5), (3,4), (3,8), (3,12), (3,20)]:
		val = a/b
		fig.add_hline(val, line_color="blue", opacity=0.2, annotation=dict(text=f"{a}/{b}", x=0.05))
	
	for i in range(len(W)):
		fracs_ = dim_fracs[i]
		N = fracs_.shape[0]
		xs = 1/density
		if i!= len(W)-1:
			dx = xs[i+1]-xs[i]
		fig.add_trace(
			go.Scatter(
				x=1/density[i]*np.ones(N)+dx*np.random.uniform(-0.1,0.1,N),
				y=fracs_,
				marker=dict(
					color='black',
					size=1,
					opacity=0.5,
				),
				mode='markers',
			)
		)
	fig.update_xaxes(showgrid=False)
	fig.update_yaxes(showgrid=False)
	fig.update_layout(showlegend=False)

# %%
