import random
from os import path
import gym
import datetime
from keras.models import Sequential
from keras.layers import Dense, Dropout, BatchNormalization
from keras.optimizers import Adam, RMSprop, SGD
# import tensorflow as tf
import pandas as pd
from collections import deque
from typing import Tuple, List, Union
from tqdm import tqdm
import numpy as np
import matplotlib.pyplot as plt
import argparse
import inspect
import json

OPTIMIZERS = {
    'Adam': Adam,
    'RMSprop': RMSprop,
    'SGD': SGD
}


class ExperienceReplay:
    def __init__(self, size: int):
        self.size: int = size
        self._exp_rep: deque = deque([], maxlen=size)

    def append(self, experience):
        self._exp_rep.append(experience)

    def sample(self, batch_size: int):
        rand_sample = random.sample(self._exp_rep, batch_size)
        dict_batch = {
            'states': np.stack([b_step[0] for b_step in rand_sample]),
            'actions': np.array([b_step[1] for b_step in rand_sample]),
            'rewards': np.array([b_step[2] for b_step in rand_sample]),
            'next_states': np.stack([b_step[3] for b_step in rand_sample]),
            'dones': np.array([b_step[4] for b_step in rand_sample])
        }
        return dict_batch

    def __len__(self):
        return self._exp_rep.__len__()


class DQN():
    def __init__(
            self, env: gym.Env, double_dqn: bool = False, hidden_dims: List[int] = [16, 32, 32, 16, 16],
            lr: float = 0.01, min_lr: float = 0.001, lr_decay: float = 0.999,
            epsilon_bounds: list[float, float] = [1.0, 0.2], eps_decay_fraction: float = 0.25, gamma: float = 0.9999,
            learning_epochs: int = 16, batch_size: int = 128, target_update_interval: int = 16,
            steps_per_epoch: int = 128,
            buffer_size: int = 2048, min_steps_learn: int = 2048, inner_activation: str = 'relu',
            verbose: Union[str, int] = 0,
            final_activation: str = 'relu', optimizer_name: str = 'Adam', loss_fn_name: str = 'mse',
            dropout: float = 0.1, batch_norm: bool = False,
            kernel_initializer: str = 'he_normal', report_interval: int = 5, save_interval: int = 200):
        assert optimizer_name in OPTIMIZERS.keys(), "Unknown optimizer"
        self.env = env
        self.double_dqn = double_dqn
        self.action_space = env.action_space.n
        self.state_space = env.observation_space.shape[0]
        self.steps_per_epoch = steps_per_epoch
        self.hidden_dims = hidden_dims
        self.target_update_interval = target_update_interval
        self.epsilon = epsilon_bounds[0]
        self.epsilons = []
        self.epsilon_bounds = epsilon_bounds
        self.eps_decay_fraction = eps_decay_fraction
        self.gamma = gamma
        self.lr = lr
        self.min_lr = min_lr
        self.lr_decay = lr_decay
        self.min_steps_learn = min_steps_learn
        self.inner_act = inner_activation
        self.final_activation = final_activation
        self.optimizer_name = optimizer_name
        self.loss_fn_name = loss_fn_name
        self.kernel_initializer = kernel_initializer
        self.verbose = verbose
        self.learning_epochs = learning_epochs
        self.batch_size = batch_size
        self.report_interval = report_interval
        self.replay_buffer = ExperienceReplay(buffer_size)
        self.save_interval = save_interval
        self.dropout = dropout
        self.bn = batch_norm
        self.q = self._build_model()
        self.q_target = self._build_model()
        self.train_log_dir = self._setup_tensorboard()
        self.opt_init_states = [var.value() for var in self.q.optimizer.variables()]
        m_args = locals().copy()
        m_args.pop('self')
        m_args.pop('env')
        with open(path.join(self.train_log_dir, 'params.json'), 'w') as f:
            f.write(json.dumps(m_args, indent=4))

        self.running_rews = deque([], maxlen=100)

        self.ckpt = tf.train.Checkpoint(step=tf.Variable(1), q=self.q, target=self.q_target)
        self.ckpt_mgr = tf.train.CheckpointManager(self.ckpt, path.join(self.train_log_dir, 'tf_ckpts'), max_to_keep=3)

        self.q_updates = []

    def _build_model(self):
        net = Sequential()
        net.add(Dense(self.hidden_dims[0], input_dim=self.state_space, activation=self.inner_act))
        for next_dim in self.hidden_dims[1:]:
            net.add(Dense(next_dim, activation=self.inner_act, kernel_initializer=self.kernel_initializer))
            net.add(Dropout(rate=self.dropout))
            if self.bn:
                net.add(BatchNormalization())
        net.add(Dense(self.env.action_space.n, activation=self.final_activation,
                      kernel_initializer=self.kernel_initializer))
        net.compile(loss=self.loss_fn_name, optimizer=OPTIMIZERS[self.optimizer_name](self.lr))
        return net

    def _save_model(self):
        self.ckpt_mgr.save()

    def _setup_tensorboard(self):
        current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        train_log_dir = 'logs/gradient_tape/' + current_time + '/train'
        self.summary_writer = tf.summary.create_file_writer(train_log_dir)
        return train_log_dir

    def _update_target(self):
        self.q_target.set_weights(self.q.get_weights())
        for val, var in zip(self.opt_init_states, self.q.optimizer.variables()):
            var.assign(val)
        self.q.optimizer.learning_rate = self.lr
        assert np.allclose(self.q.optimizer.lr.numpy(), self.lr)

    def _update_eps(self):
        # self.epsilon = 0.99 * self.epsilon
        if (self.epoch + 1) / self.n_epochs < self.eps_decay_fraction:
            final_decay_episode = int(self.n_epochs * self.eps_decay_fraction)
            self.epsilon = self.epsilon_bounds[0] - (self.epsilon_bounds[0] - self.epsilon_bounds[1]) * (
                    (self.epoch + 1) / final_decay_episode)
        if self.lr > self.min_lr:
            self.lr *= self.lr_decay

    def get_action(self, state, epsilon=None):
        epsilon = self.epsilon if epsilon is None else epsilon
        if epsilon > random.random():
            action = self.env.action_space.sample()
        else:
            action = np.argmax(self.q(state))
        return action

    def learn(self):
        batch = self.replay_buffer.sample(self.batch_size)
        gamma = (1 - batch['dones']) * self.gamma
        if self.double_dqn:
            decoupled_action = np.argmax(self.q(batch['next_states']), axis=1)
            y = batch['rewards'] + gamma * tf.gather(params=self.q_target(batch['next_states']),
                                                     indices=decoupled_action,
                                                     batch_dims=1)
        else:
            y = batch['rewards'] + gamma * np.max(self.q_target(batch['next_states']), axis=1)
        y_q = self.q(batch['states']).numpy()  # Predict Qs on all actions
        y_q[np.arange(len(y_q)).tolist(), batch['actions'].astype(
            int).tolist()] = y  # Change the values of the actual actions to target (y)
        loss = self.q.fit(batch['states'], y_q, batch_size=self.batch_size, verbose=0).history[
            'loss']  # loss != 0 only on actual actions takes
        return loss

    def collect_batch(self, n_steps, epsilon=None, show_progress=False):
        ep_lengths = []
        episodes = 0
        episode_steps = 0
        state = self.env.reset()
        ep_reward = 0
        if show_progress:
            pbar = tqdm(total=n_steps)
        for step_num in range(
                10000):  # larger than n_steps to make sure we finish the episodes, but no too large so infinite episodes will not result in infinite loops
            action = self.get_action(np.expand_dims(state, 0), epsilon)
            next_state, reward, done, info = self.env.step(action)
            if done:
                # This will throw a warning, but it is the only way to know if the episode was truncated or terminated
                _, tmp_reward, _, _ = self.env.step(self.env.action_space.sample())
                if tmp_reward < 1.0:
                    reward = -10
            episode_steps += 1
            self.replay_buffer.append([state, action, reward, next_state, done])
            assert len(self.replay_buffer) <= self.replay_buffer.size
            state = next_state
            if done:
                state = self.env.reset()
                episodes += 1
                ep_lengths.append(episode_steps)
                episode_steps = 0
                if step_num >= n_steps:
                    break
            ep_reward += reward
            if show_progress and step_num % 10 == 0:
                pbar.update(10)
        if show_progress:
            pbar.close()
        ep_lengths.append(episode_steps)
        return ep_reward / episodes, sum(ep_lengths) / len(ep_lengths)

    def evaluate(self, n_ep=5):
        rewards = []
        ep_lengths = []
        for _ in range(n_ep):
            episode_steps = 0
            rewards.append(0)
            state = self.env.reset()
            for step_num in range(500):
                action = np.argmax(self.q(np.expand_dims(state, 0)))
                next_state, reward, done, info = self.env.step(action)
                rewards[-1] += 1
                if done:
                    ep_lengths.append(episode_steps)
                    break
                state = next_state
                episode_steps += 1
        return (rewards, ep_lengths)

    def output_report(self):
        fig, ax = plt.subplots(2, 2, figsize=(10, 10))
        ax = ax.ravel()
        ax[0].plot(self.rews)
        ax[0].set_title('Average episode Reward')
        ax[1].plot(self.losses)
        ax[1].set_title('Training Loss')
        ax[2].plot(self.epsilons)
        ax[2].set_title('Epsilon')
        plt.savefig('progress.png')
        plt.close('all')

    def train(self, n_epochs):
        self.ckpt.step.assign_add(1)
        print('collecting decorrelation steps')
        avg_rew, avg_len = self.collect_batch(self.min_steps_learn, epsilon=1, show_progress=True)
        self.n_epochs = n_epochs
        print(f'Training for {n_epochs} epochs')
        for ep in tqdm(range(n_epochs)):
            self.epoch = ep
            self._update_eps()
            _, _ = self.collect_batch(self.steps_per_epoch)
            loss = self.learn()
            if ep % self.report_interval == 0:
                rews, lengths = self.evaluate()
                self.running_rews.extend(rews)
                with self.summary_writer.as_default():
                    tf.summary.scalar('loss', loss[0], step=ep)
                    tf.summary.scalar('Avg_reward', np.mean(rews), step=ep)
                    tf.summary.scalar('Avg_len', np.mean(lengths), step=ep)
                    tf.summary.scalar('Running_Avg_Rew', np.mean(self.running_rews), step=ep)
                    tf.summary.scalar('Epsilon', self.epsilon, step=ep)
                    tf.summary.scalar('Learning_rate', self.q.optimizer.lr.numpy(), step=ep)
            if ep % self.target_update_interval == 0:
                self._update_target()
            if ep % self.save_interval == 0:
                self._save_model()
            if np.mean(self.running_rews) > 450:
                self._save_model()
                print('Reached Target!!!!')
                with self.summary_writer.as_default():
                    tf.summary.scalar('loss', loss[0], step=ep)
                    tf.summary.scalar('Avg_reward', np.mean(rews), step=ep)
                    tf.summary.scalar('Avg_len', np.mean(lengths), step=ep)
                    tf.summary.scalar('Running_Avg_Rew', np.mean(self.running_rews), step=ep)
                    tf.summary.scalar('Epsilon', self.epsilon, step=ep)
                    tf.summary.scalar('Learning_rate', self.q.optimizer.lr.numpy(), step=ep)
                break


def parse_args():
    fn_args = inspect.get_annotations(DQN.__init__)
    signature = inspect.signature(DQN.__init__)

    args = {k: (fn_args[k], v.default) for k, v in signature.parameters.items() if
            v.default is not inspect.Parameter.empty}
    parser = argparse.ArgumentParser(description='DQN implementation in TF baaaaa')

    for arg in args.keys():
        parser.add_argument(f'--{arg}', type=args[arg][0], default=args[arg][1], required=False)
    args = vars(parser.parse_args())
    return (args)


def basic_plotter():
    csv_filename = 'run-gradient_tape_20221130-161126_train-tag-Avg_reward.csv'
    rolling = 72

    df = pd.read_csv(csv_filename, header=0)
    final_step = np.where(df.Value.rolling(rolling).mean() > 475.0)[0][0]
    f, ax = plt.subplots(1, 1)
    ax.plot(df.Step.iloc[:final_step], df.Value.iloc[:final_step], label='Episode Reward')
    ax.plot(df.Step.iloc[:final_step], df.Value.rolling(rolling).mean().iloc[:final_step],
            label='Rolling average episode reward')
    # ax.plot(df.Step, df.Value, label='Episode Reward')
    # ax.plot(df.Step, df.Value.rolling(rolling).mean(),
    #         label='Rolling average episode reward')
    ax.plot([0, df.Step.iloc[final_step]], [475, 475], 'k--', label='Target length')

    ax.set_xlabel('Training Step')
    ax.set_ylabel('Episode Reward')
    plt.legend(loc=[0.02, 0.6])
    plt.suptitle('Double DQN')
    plt.tight_layout()
    plt.savefig('Plots/DQN/Double_dqn.jpg')
    print('donwe')


if __name__ == '__main__':
    # args = parse_args(args=[])
    args = parse_args()
    env = gym.make('CartPole-v1')
    device = tf.test.gpu_device_name() if len(tf.config.list_physical_devices('GPU')) > 0 else '/device:CPU:0'
    with tf.device(device):
        print(f"Device: {device}")
        dqn = DQN(env, **args)
        dqn.train(10000)
    # basic_plotter()
