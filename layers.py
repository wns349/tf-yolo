import numpy as np
import tensorflow as tf


class conv2d(object):
    def __init__(self, prev_self, filter_h, filter_w, no_filters, stride, padding="SAME", batch_normalize=True):
        self.batch_normalize = batch_normalize

        in_size = int(prev_self.out.shape.as_list()[-1])
        self.weights_shape = [filter_h, filter_w, in_size, no_filters]
        self.weights_shape_darknet = [no_filters, in_size, filter_h, filter_w]
        self.biases_shape = [no_filters]

        self.batch_norm_shape = [no_filters] if self.batch_normalize else [0]
        self.weight_size = int(np.prod(self.biases_shape)
                               + np.prod(self.weights_shape)
                               + np.prod(self.batch_norm_shape) * 3.)

        self.weights = tf.Variable(tf.random_normal(self.weights_shape, stddev=0.5))
        self.biases = tf.Variable(tf.zeros(self.biases_shape))
        self.out = tf.nn.conv2d(prev_self.out, self.weights, strides=[1, stride, stride, 1], padding=padding)
        if self.batch_normalize:
            self.gamma = tf.Variable(tf.zeros(self.batch_norm_shape))
            self.mean = tf.Variable(tf.zeros(self.batch_norm_shape))
            self.variance = tf.Variable(tf.zeros(self.batch_norm_shape))
            self.out = tf.nn.batch_normalization(self.out, mean=self.mean, variance=self.variance, scale=self.gamma,
                                                 variance_epsilon=1e-5, offset=None)

        self.out = tf.nn.bias_add(self.out, self.biases)

    def load_weights(self, tf_session, offset, weights_path):
        values = np.memmap(weights_path, shape=self.weight_size, offset=offset, dtype=np.float32)
        i = 0
        b = values[i:i + np.prod(self.biases_shape)].reshape(self.biases_shape)
        i += np.prod(self.biases_shape)
        tf_session.run(self.biases.assign(b))
        if self.batch_normalize:
            g = values[i:i + np.prod(self.batch_norm_shape)].reshape(self.batch_norm_shape)
            i += np.prod(self.batch_norm_shape)
            tf_session.run(self.gamma.assign(g))
            m = values[i:i + np.prod(self.batch_norm_shape)].reshape(self.batch_norm_shape)
            i += np.prod(self.batch_norm_shape)
            tf_session.run(self.mean.assign(m))
            v = values[i:i + np.prod(self.batch_norm_shape)].reshape(self.batch_norm_shape)
            i += np.prod(self.batch_norm_shape)
            tf_session.run(self.variance.assign(v))

        w = values[i:i + np.prod(self.weights_shape_darknet)].reshape(self.weights_shape_darknet)
        w = np.transpose(w, (2, 3, 1, 0))  # darknet to tf shape
        i += np.prod(self.weights_shape)
        tf_session.run(self.weights.assign(w))

        return i * 4


class leaky_relu(object):
    def __init__(self, prev_layer, alpha=0.1):
        self.out = tf.nn.leaky_relu(prev_layer.out, alpha=alpha)


class max_pool2d(object):
    def __init__(self, prev_layer, ksize, stride, padding="SAME"):
        self.out = tf.nn.max_pool(prev_layer.out, ksize=[1, ksize, ksize, 1], strides=[1, stride, stride, 1],
                                  padding=padding)


class input_layer(object):
    def __init__(self, shape, name="input"):
        self.out = tf.placeholder(dtype=tf.float32, shape=shape, name=name)
