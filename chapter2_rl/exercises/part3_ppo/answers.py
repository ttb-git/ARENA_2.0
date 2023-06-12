# %%
import os

os.environ["ACCELERATE_DISABLE_RICH"] = "1"
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import random
import time
import sys
import re
import pandas as pd
from dataclasses import dataclass
from tqdm import tqdm
import numpy as np
from numpy.random import Generator
import plotly.express as px
import torch as t
from torch import Tensor
from torch.optim.optimizer import Optimizer
from torch.utils.data import Dataset
import gym
from gym.envs.classic_control.cartpole import CartPoleEnv
import torch.nn as nn
import torch.optim as optim
from torch.distributions.categorical import Categorical
from gym.spaces import Discrete
import einops
import copy
from pathlib import Path
from typing import List, Tuple, Dict, Any, Union, Callable, Optional
from jaxtyping import Float, Int, Bool
import pytorch_lightning as pl
from pytorch_lightning.loggers import WandbLogger, CSVLogger
import wandb

# Make sure exercises are in the path
chapter = r"chapter2_rl"
exercises_dir = Path(f"{os.getcwd().split(chapter)[0]}/{chapter}/exercises").resolve()
section_dir = exercises_dir / "part3_ppo"
if str(exercises_dir) not in sys.path:
    sys.path.append(str(exercises_dir))

from part1_intro_to_rl.utils import make_env
from part2_dqn.utils import set_global_seeds
from part2_dqn.solutions import Probe1, Probe2, Probe3, Probe4, Probe5
import part3_ppo.utils as utils
import part3_ppo.tests as tests
from plotly_utils import plot_cartpole_obs_and_dones

# Register our probes from last time
for idx, probe in enumerate([Probe1, Probe2, Probe3, Probe4, Probe5]):
    gym.envs.registration.register(id=f"Probe{idx+1}-v0", entry_point=probe)

Arr = np.ndarray

device = t.device("cuda" if t.cuda.is_available() else "cpu")

MAIN = __name__ == "__main__"


# %%
@dataclass
class PPOArgs:
    exp_name: str = "PPO_Implementation"
    seed: int = 1
    cuda: bool = t.cuda.is_available()
    log_dir: str = "logs"
    use_wandb: bool = False
    wandb_project_name: str = "PPOCart"
    wandb_entity: str = None
    capture_video: bool = True
    env_id: str = "CartPole-v1"
    total_timesteps: int = 500000
    learning_rate: float = 0.00025
    num_envs: int = 4
    num_steps: int = 128
    gamma: float = 0.99
    gae_lambda: float = 0.95
    num_minibatches: int = 4
    batches_per_epoch: int = 4
    clip_coef: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    batch_size: int = 512
    minibatch_size: int = 128

    def __post_init__(self):
        assert (
            self.batch_size % self.minibatch_size == 0
        ), "batch_size must be divisible by minibatch_size"
        self.total_epochs = self.total_timesteps // (self.num_steps * self.num_envs)
        self.total_training_steps = (
            self.total_epochs
            * self.batches_per_epoch
            * (self.batch_size // self.minibatch_size)
        )


args = PPOArgs(minibatch_size=256)
utils.arg_help(args)


# %%
def layer_init(layer: nn.Linear, std=np.sqrt(2), bias_const=0.0):
    t.nn.init.orthogonal_(layer.weight, std)
    t.nn.init.constant_(layer.bias, bias_const)
    return layer


def get_actor_and_critic(envs: gym.vector.SyncVectorEnv) -> Tuple[nn.Module, nn.Module]:
    """
    Returns (actor, critic), the networks used for PPO.
    """
    obs_shape = envs.single_observation_space.shape
    num_obs = np.array(obs_shape).prod()
    num_actions = envs.single_action_space.n

    mid_dim = 64
    final_std = 0.01
    actor = t.nn.Sequential(
        layer_init(t.nn.Linear(in_features=num_obs, out_features=mid_dim)),
        t.nn.Tanh(),
        layer_init(t.nn.Linear(in_features=mid_dim, out_features=mid_dim)),
        t.nn.Tanh(),
        layer_init(t.nn.Linear(in_features=mid_dim, out_features=num_actions), std=final_std),
    )

    critic = t.nn.Sequential(
        layer_init(t.nn.Linear(in_features=num_obs, out_features=mid_dim)),
        t.nn.Tanh(),
        layer_init(t.nn.Linear(in_features=mid_dim, out_features=mid_dim)),
        t.nn.Tanh(),
        layer_init(t.nn.Linear(in_features=mid_dim, out_features=1), std=1),
    )
    return actor, critic


tests.test_get_actor_and_critic(get_actor_and_critic)


# %%

@t.inference_mode()
def compute_advantages(
    next_value: t.Tensor,
    next_done: t.Tensor,
    rewards: t.Tensor,
    values: t.Tensor,
    dones: t.Tensor,
    gamma: float,
    gae_lambda: float,
) -> t.Tensor:
    '''Compute advantages using Generalized Advantage Estimation.
    next_value: shape (env,)
    next_done: shape (env,)
    rewards: shape (buffer_size, env)
    values: shape (buffer_size, env)
    dones: shape (buffer_size, env)
    Return: shape (buffer_size, env)
    '''
    # A_pi(state, action) = Q_pi(state, action) - V_pi(state, action)
    # A estimate_t = delta_t + delta_{t+1} + ... delta_{T - 1}
    
    # TD errors: r_{t+1} + gamma * V_pi(S_{t+1}) - V_pi(S_t)
    buffer_size, env = rewards.shape

    deltas = t.zeros_like(rewards)
    next_values = t.concat([values, next_value.unsqueeze(0)], dim=0)[1:]
    next_dones = t.concat([dones, next_done.unsqueeze(0)], dim=0)[1:]
    still_alives = 1.0 - next_dones

    assert values.shape[0] == buffer_size
    assert dones.shape[0] == buffer_size
    assert still_alives.shape[0] == buffer_size

    deltas = rewards + gamma * next_values * still_alives - values

    assert deltas.shape[0] == buffer_size
    assert deltas.shape[1] == env

    A_estimates = t.zeros_like(rewards)
    A_estimates[-1] = deltas[-1]

    for timestep in range(buffer_size-2, -1, -1):
        scale = gamma * gae_lambda * (1 - dones[timestep+1])
        A_estimates[timestep] = deltas[timestep] + scale * A_estimates[timestep+1]

    return A_estimates


tests.test_compute_advantages(compute_advantages)
# %%