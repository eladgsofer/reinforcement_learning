import gym
import numpy as np
import tensorflow.compat.v1 as tf
import collections
import os
from datetime import datetime
import time
import pdb

# optimized for Tf2
tf.disable_v2_behavior()

algorithm_name = "actor_critic"


# Actor
class PolicyNetwork:
    def __init__(self, state_size, action_size, learning_rate, name='policy_network'):
        self.state_size = state_size
        self.action_size = action_size
        self.learning_rate = learning_rate

        with tf.variable_scope(name):
            self.state = tf.placeholder(tf.float32, [None, self.state_size], name="state")
            self.advantage_delta = tf.placeholder(tf.float32, name="advantage_delta")
            self.I_factor = tf.placeholder(tf.float32, name="I_factor")

            tf2_initializer = tf.keras.initializers.glorot_normal(seed=0)
            self.W1 = tf.get_variable("W1", [self.state_size, 12], initializer=tf2_initializer)
            self.b1 = tf.get_variable("b1", [12], initializer=tf2_initializer)
            self.W2 = tf.get_variable("W2", [12, self.action_size], initializer=tf2_initializer)
            self.b2 = tf.get_variable("b2", [self.action_size], initializer=tf2_initializer)

            self.Z1 = tf.add(tf.matmul(self.state, self.W1), self.b1)
            self.A1 = tf.nn.relu(self.Z1)
            self.output = tf.add(tf.matmul(self.A1, self.W2), self.b2)

            # Softmax probability distribution over actions
            self.actions_distribution = tf.squeeze(tf.nn.softmax(self.output))
            self.actions_log_probs = tf.math.log(self.actions_distribution)

            # Loss calculation - to acheive gradient ascent we wan't to minimize the negative of the loss.
            self.loss = self.I_factor * -tf.math.reduce_sum(self.advantage_delta * self.actions_log_probs)
            self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate).minimize(self.loss)


# Critic
class ValueNetwork:
    def __init__(self, state_size, learning_rate, name='state_value_network'):
        self.state_size = state_size
        self.learning_rate = learning_rate

        with tf.variable_scope(name):
            # Place holders for future calculation
            self.state = tf.placeholder(tf.float32, [None, self.state_size], name="state")
            self.I_factor = tf.placeholder(tf.float32, name="I_factor")
            self.advantage_delta = tf.placeholder(tf.float32, name="advantage_delta")

            tf2_initializer = tf.keras.initializers.glorot_normal(seed=0)
            self.W1 = tf.get_variable("W1", [self.state_size, 64], initializer=tf2_initializer)
            self.b1 = tf.get_variable("b1", [64], initializer=tf2_initializer)
            self.W2 = tf.get_variable("W2", [64, 16], initializer=tf2_initializer)
            self.b2 = tf.get_variable("b2", [16], initializer=tf2_initializer)
            self.W3 = tf.get_variable("W3", [16, 1], initializer=tf2_initializer)
            self.b3 = tf.get_variable("b3", [1], initializer=tf2_initializer)

            self.Z1 = tf.add(tf.matmul(self.state, self.W1), self.b1)
            self.A1 = tf.nn.relu(self.Z1)
            self.Z2 = tf.add(tf.matmul(self.A1, self.W2), self.b2)
            self.A2 = tf.nn.relu(self.Z2)
            self.output = tf.add(tf.matmul(self.A2, self.W3), self.b3)

            # Loss calculation - to acheive gradient ascent we wan't to minimize the negative of the loss.
            self.loss = -self.advantage_delta * self.I_factor * self.output
            self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate).minimize(self.loss)


def run(discount_factor, policy_learning_rate, sv_learning_rate):
    env = gym.make('CartPole-v1')
    np.random.seed(SEED)
    env.seed(SEED)
    tf.set_random_seed(SEED)
    rewards, mean_rewards, losses = [], [], []
    # Define hyperparameters
    state_size = 4
    action_size = env.action_space.n

    max_episodes = 5000
    max_steps = 501
    discount_factor = discount_factor
    policy_learning_rate = policy_learning_rate
    sv_learning_rate = sv_learning_rate
    render = False

    # Initialize the policy and the state-value network
    tf.reset_default_graph()

    policy = PolicyNetwork(state_size, action_size, policy_learning_rate)
    state_value = ValueNetwork(state_size, sv_learning_rate)

    # Start training the agent with REINFORCE algorithm
    with tf.Session() as sess:

        sess.run(tf.global_variables_initializer())
        solved = False
        episode_rewards = np.zeros(max_episodes)
        average_rewards = 0.0
        stable = False
        # pdb.set_trace()
        for episode in range(max_episodes):

            # pdb.set_trace
            state = env.reset()
            state = state.reshape([1, state_size])
            I_factor = 1

            for step in range(max_steps):
                # pdb.set_trace()

                # Take action A ~ pi(*|S,thetha) and observe S',R.
                actions_distribution = sess.run(policy.actions_distribution, {policy.state: state})
                try:
                    action = np.random.choice(np.arange(len(actions_distribution)), p=actions_distribution)
                except:
                    # pdb.set_trace()
                    print("hey")
                next_state, reward, done, _ = env.step(action)
                next_state = next_state.reshape([1, state_size])

                # action_one_hot = np.zeros(action_size)
                # action_one_hot[action] = 1
                episode_rewards[episode] += reward

                if render:
                    env.render()

                # Calculate state-value output for current state
                feed_dict = {state_value.state: state}
                value_current_state = sess.run(state_value.output, feed_dict)

                # Calculate state-value output for next state
                feed_dict = {state_value.state: next_state}
                value_next_state = sess.run(state_value.output, feed_dict)

                # Calculate advantage
                target = reward if done else reward + discount_factor * value_next_state
                advantage_delta = target - value_current_state

                # Update the state_value network weights
                feed_dict = {state_value.state: state, state_value.I_factor: I_factor,
                             state_value.advantage_delta: advantage_delta}
                _, loss_state = sess.run([state_value.optimizer, state_value.loss], feed_dict)

                # Update the policy network weights
                feed_dict = {policy.state: state, policy.I_factor: I_factor,
                             policy.advantage_delta: advantage_delta}
                if stable:
                  # We prevent the network weights from changing after it is stable
                  loss_policy = sess.run(policy.loss, feed_dict)
                else:
                  _, loss_policy = sess.run([policy.optimizer, policy.loss], feed_dict)

                if done:

                    if episode > 98:
                        average_rewards = np.mean(episode_rewards[(episode - 99):episode + 1])
                    lst_5_avg = np.mean(episode_rewards[(episode - 5):episode + 1])
                    if lst_5_avg > 475:
                        stable = True

                    print(
                        "Episode {} Reward: {} Average over 100 episodes: {}".format(episode, episode_rewards[episode],
                                                                                     round(average_rewards, 2)))
                    if average_rewards > 475:
                        print(' Solved at episode: ' + str(episode))
                        solved = True
                    break

                # I <- gamma*I
                I_factor *= discount_factor

                # S<-S'
                state = next_state

            if solved:
                break

            rewards.append(episode_rewards[episode])
            mean_rewards.append(average_rewards)
            losses.append(loss_policy)
    return episode, rewards, mean_rewards, losses


if __name__ == '__main__':
    SEED = 42
    # optimal_sv_lr = 0.0007
    # optimal_policy_lr = 0.0005

    # optimal_sv_lr = tf.keras.optimizers.schedules.ExponentialDecay(
    # initial_learning_rate,
    # decay_steps=100000,
    # decay_rate=0.96,
    # staircase=True)

    optimal_sv_lr = 0.0007
    optimal_policy_lr = 0.01

    optimal_df = 0.99
    algorithm_name = "actor_critic"
    last_episode, rewards, mean_rewards, losses = run(discount_factor=optimal_df,
                                                      policy_learning_rate=optimal_policy_lr,
                                                      sv_learning_rate=optimal_sv_lr)
    with open('optimal_{}.npy'.format(algorithm_name), 'wb') as f:
        np.save(f, last_episode)
        np.save(f, rewards)
        np.save(f, mean_rewards)
        np.save(f, losses)