"""
Copyright (c) 2016 Gianluca Gerard

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Portions of the code are
Copyright (c) 2010--2015, Deep Learning Tutorials Development Team
All rights reserved.
"""

from __future__ import print_function, division

import timeit
import datetime
import sys
import os

import matplotlib.pyplot as plt

import numpy
import theano
from theano import tensor
#from theano.tensor.shared_randomstreams import RandomStreams
from theano.sandbox.rng_mrg import MRG_RandomStreams as RandomStreams
#from theano.compile.nanguardmode import NanGuardMode

from scipy import stats

from utils import get_minibatches_idx
from utils import find_unique_classes
from utils import prepare_TCGA_datafiles
from utils import import_TCGA_data

from rbm import RBM
from rbm import GRBM

from MNIST import MNIST

class HiddenLayer(object):
    def __init__(self, rng, input, n_in, n_out, W=None, b=None,
                 activation=tensor.tanh):
        """
        Typical hidden layer of a MLP: units are fully-connected and have
        sigmoidal activation function. Weight matrix W is of shape (n_in,n_out)
        and the bias vector b is of shape (n_out,).

        Originally from: http://deeplearning.net/tutorial/code/mlp.py

        NOTE : The nonlinearity used here is tanh

        Hidden unit activation is given by: tanh(dot(input,W) + b)

        :type rng: numpy.random.RandomState
        :param rng: a random number generator used to initialize weights

        :type input: theano.tensor.dmatrix
        :param input: a symbolic tensor of shape (n_examples, n_in)

        :type n_in: int
        :param n_in: dimensionality of input

        :type n_out: int
        :param n_out: number of hidden units

        :type activation: theano.Op or function
        :param activation: Non linearity to be applied in the hidden
                           layer
        """
        self.input = input

        # `W` is initialized with `W_values` which is uniformely sampled
        # from sqrt(-6./(n_in+n_hidden)) and sqrt(6./(n_in+n_hidden))
        # for tanh activation function
        # the output of uniform if converted using asarray to dtype
        # theano.config.floatX so that the code is runable on GPU
        # Note : optimal initialization of weights is dependent on the
        #        activation function used (among other things).
        #        For example, results presented in [Xavier10] suggest that you
        #        should use 4 times larger initial weights for sigmoid
        #        compared to tanh
        #        We have no info for other function, so we use the same as
        #        tanh.
        if W is None:
            W_values = numpy.asarray(
                rng.uniform(
                    low=-numpy.sqrt(6. / (n_in + n_out)),
                    high=numpy.sqrt(6. / (n_in + n_out)),
                    size=(n_in, n_out)
                ),
                dtype=theano.config.floatX
            )
            if activation == theano.tensor.nnet.sigmoid:
                W_values *= 4

            W = theano.shared(value=W_values, name='W', borrow=True)

        if b is None:
            b_values = numpy.zeros((n_out,), dtype=theano.config.floatX)
            b = theano.shared(value=b_values, name='b', borrow=True)

        self.W = W
        self.b = b

        self.activation = activation

        lin_output = tensor.dot(input, self.W) + self.b
        self.output = (
            lin_output if self.activation is None
            else self.activation(lin_output)
        )

        # parameters of the model
        self.params = [self.W, self.b]

class DBN(object):
    """Deep Belief Network

    A deep belief network is obtained by stacking several RBMs on top of each
    other. The hidden layer of the RBM at layer `i` becomes the input of the
    RBM at layer `i+1`. The first layer GRBM gets as input the input of the
    network, and the hidden layer of the last RBM represents the output.
    """

    def __init__(self, numpy_rng=None, theano_rng=None, input=None, n_ins=784,
                 gauss=True,
                 hidden_layers_sizes=[400], n_outs=40,
                 W_list=None, b_list=None):
        """This class is made to support a variable number of layers.

        Originally from: http://deeplearning.net/tutorial/code/DBN.py

        :type numpy_rng: numpy.random.RandomState
        :param numpy_rng: numpy random number generator used to draw initial
                    weights

        :type theano_rng: theano.tensor.shared_randomstreams.RandomStreams
        :param theano_rng: Theano random generator; if None is given one is
                           generated based on a seed drawn from `rng`

        :type n_ins: int
        :param n_ins: dimension of the input to the DBN

        :type gauss: bool
        :param gauss: True if the first layer is Gaussian otherwise
                      the first layer is Binomial

        :type hidden_layers_sizes: list of ints
        :param hidden_layers_sizes: intermediate layers size, must contain
                               at least one value

        :type n_outs: int
        :param n_outs: dimension of the output of the network
        """

        self.n_ins = n_ins
        self.sigmoid_layers = []
        self.rbm_layers = []
        self.params = []
        self.stacked_layers_sizes = hidden_layers_sizes + [n_outs]
        self.n_layers = len(self.stacked_layers_sizes)

        assert self.n_layers > 0

        if numpy_rng is None:
            numpy_rng = numpy.random.RandomState(123)

        if theano_rng is None:
            theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))

        # allocate symbolic variables for the data

        # the data is presented as rasterized images
        self.x = tensor.matrix('x')

        # The DBN is an MLP, for which all weights of intermediate
        # layers are shared with a different RBM.  We will first
        # construct the DBN as a deep multilayer perceptron, and when
        # constructing each sigmoidal layer we also construct an RBM
        # that shares weights with that layer. During pretraining we
        # will train these RBMs (which will lead to chainging the
        # weights of the MLP as well).

        for i in range(self.n_layers):
            # construct the sigmoidal layer

            # the size of the input is either the number of hidden
            # units of the layer below or the input size if we are on
            # the first layer
            if i == 0:
                input_size = n_ins
            else:
                input_size = self.stacked_layers_sizes[i - 1]

            # the input to this layer is either the activation of the
            # hidden layer below or the input of the DBN if you are on
            # the first layer
            if i == 0:
                if input is None:
                    layer_input = self.x
                else:
                    layer_input = input
            else:
                layer_input = self.sigmoid_layers[-1].output

            n_in = input_size
            n_out= self.stacked_layers_sizes[i]

            print('Adding a layer with %i input and %i outputs' %
                  (n_in, n_out))

            if W_list is None:
                W = numpy.asarray(numpy_rng.uniform(
                                low=-4.*numpy.sqrt(6. / (n_in + n_out)),
                                high=4.*numpy.sqrt(6. / (n_in + n_out)),
                                size=(n_in, n_out)
                             ),dtype=theano.config.floatX)
            else:
                W = W_list[i]

            if b_list is None:
                b = numpy.zeros((n_out,), dtype=theano.config.floatX)
            else:
                b = b_list[i]

            sigmoid_layer = HiddenLayer(rng=numpy_rng,
                                        input=layer_input,
                                        n_in=n_in,
                                        n_out=n_out,
                                        W=theano.shared(W,name='W',borrow=True),
                                        b=theano.shared(b,name='b',borrow=True),
                                        activation=tensor.nnet.sigmoid)

            # add the layer to our list of layers
            self.sigmoid_layers.append(sigmoid_layer)

            # its arguably a philosophical question...  but we are
            # going to only declare that the parameters of the
            # sigmoid_layers are parameters of the DBN. The visible
            # biases in the RBM are parameters of those RBMs, but not
            # of the DBN.
            self.params.extend(sigmoid_layer.params)

            # Construct an RBM that shared weights with this layer
            if i==0 and gauss:
                rbm_layer = GRBM(numpy_rng=numpy_rng,
                                theano_rng=theano_rng,
                                input=layer_input,
                                n_visible=input_size,
                                n_hidden=self.stacked_layers_sizes[i],
                                W=sigmoid_layer.W,
                                hbias=sigmoid_layer.b)
            else:
                rbm_layer = RBM(numpy_rng=numpy_rng,
                                theano_rng=theano_rng,
                                input=layer_input,
                                n_visible=input_size,
                                n_hidden=self.stacked_layers_sizes[i],
                                W=sigmoid_layer.W,
                                hbias=sigmoid_layer.b)

            self.rbm_layers.append(rbm_layer)

    def number_of_nodes(self):
        return [self.n_ins] + self.stacked_layers_sizes

    def inspect_inputs(self, i, node, fn):
        print(i, node, "input(s) value(s):", [input[0] for input in fn.inputs],
              end='\n')

    def inspect_outputs(self, i, node, fn):
        print(" output(s) value(s):", [output[0] for output in fn.outputs])

    def get_output(self, input, layer=-1):
        if input is not None:
            fn = theano.function(inputs=[],
                                 outputs=self.sigmoid_layers[layer].output,
                                 givens={
                                     self.x: input
                                 })
            return fn()
        else:
            return None

    def pretraining_functions(self, train_set_x, validation_set_x,
                              batch_size, k, lambda_1 = 0.0, lambda_2 = 0.1, monitor=False):
        '''Generates a list of functions, for performing one step of
        gradient descent at a given layer. The function will require
        as input the minibatch index, and to train an RBM you just
        need to iterate, calling the corresponding function on all
        minibatch indexes.

        :type train_set_x: theano.tensor.TensorType
        :param train_set_x: Shared var. that contains all datapoints used
                            for training the RBM
        :type batch_size: int
        :param batch_size: size of a [mini]batch
        :param k: number of Gibbs steps to do in CD-k / PCD-k

        '''

        # index to a [mini]batch
        indexes = tensor.lvector('indexes')  # index to a minibatch
        learning_rate = tensor.scalar('lr', dtype=theano.config.floatX)  # learning rate to use
        momentum = tensor.scalar('momentum', dtype=theano.config.floatX)

        # TODO: deal with batch_size of 1
        assert batch_size > 1

        pretrain_fns = []
        free_energy_gap_fns = []
        for i, rbm in enumerate(self.rbm_layers):
            # get the cost and the updates list
            # using CD-k here (persisent=None) for training each RBM.
            # TODO: change cost function to reconstruction error
            if isinstance(rbm, GRBM):
                cost, updates = rbm.get_cost_updates(learning_rate,
                                                     lambda_1=lambda_1,
                                                     lambda_2 = lambda_2,
                                                     batch_size=batch_size,
                                                     persistent=None, k=k)
            else:
                cost, updates = rbm.get_cost_updates(learning_rate,
                                                     weightcost = 0.0002,
                                                     batch_size=batch_size,
                                                     persistent=None, k=k)

            # compile the theano function
            if monitor:
                mode = theano.compile.MonitorMode(pre_func=self.inspect_inputs)
            else:
                mode = theano.config.mode

            fn = theano.function(
                inputs=[indexes, momentum, theano.In(learning_rate)],
                outputs=cost,
                updates=updates,
                givens={
                        self.x: train_set_x[indexes],
                        rbm.momentum: momentum
                },
                mode = mode
    #           mode=NanGuardMode(nan_is_error=True, inf_is_error=True, big_is_error=True)
            )

            # append `fn` to the list of functions
            pretrain_fns.append(fn)

            train_sample = tensor.matrix('train_smaple', dtype=theano.config.floatX)
            test_sample = tensor.matrix('validation_smaple', dtype=theano.config.floatX)

            feg = rbm.free_energies(train_sample, test_sample)

            # Obtain the input of layer i as the output of the previous
            # layer
            fn = theano.function(
                inputs=[train_sample, test_sample],
                outputs=feg,
                mode=mode
            )

            free_energy_gap_fns.append(fn)

        return pretrain_fns, free_energy_gap_fns

    def pretraining(self, train_set_x, validation_set_x,
                    batch_size, k,
                    pretraining_epochs, pretrain_lr,
                    lambda_1 = 0.0,
                    lambda_2 = 0.1,
                    monitor=False, graph_output=False):
        #########################
        # PRETRAINING THE MODEL #
        #########################

        print('... getting the pretraining functions')
        print('Training set sample size %i' % train_set_x.get_value().shape[0])
        if validation_set_x is not None:
            print('Validation set sample size %i' % validation_set_x.get_value().shape[0])

        pretraining_fns, free_energy_gap_fns = self.pretraining_functions(train_set_x=train_set_x,
                                                     validation_set_x=validation_set_x,
                                                     batch_size=batch_size,
                                                     k=k,
                                                     lambda_1=lambda_1,
                                                     lambda_2=lambda_2,
                                                     monitor=monitor)

        print('... pre-training the model')
        start_time = timeit.default_timer()
        # Pre-train layer-wise

        if graph_output:
            plt.ion()

        n_data = train_set_x.get_value().shape[0]

        if validation_set_x is not None:
            t_set = train_set_x.get_value(borrow=True)
            v_set = validation_set_x.get_value(borrow=True)

        # early-stopping parameters

        patience_increase = 2  # wait this much longer when a new best is
        # found
        improvement_threshold = 0.995  # a relative improvement of this much is
        # considered significant

        # go through this many
        # minibatches before checking the network
        # on the validation set; in this case we
        # check every epoch

        idx_minibatches, minibatches = get_minibatches_idx(n_data,
                                                           batch_size,
                                                           shuffle=True)

        n_train_batches = idx_minibatches[-1] + 1

        for i in range(self.n_layers):
            if graph_output:
                plt.figure(i+1)

            if isinstance(self.rbm_layers[i], GRBM):
                momentum = 0.0
            else:
                momentum = 0.6

            # go through pretraining epochs
            best_cost = numpy.inf
            epoch = 0
            done_looping = False

            patience = pretraining_epochs[i]  # look as this many examples regardless
            validation_frequency = min(20 * n_train_batches, patience // 2)
            print('Validation frequency: %d' % validation_frequency)

            while (epoch < pretraining_epochs[i]) and (not done_looping):
                epoch = epoch + 1

                idx_minibatches, minibatches = get_minibatches_idx(n_data,
                                                                   batch_size,
                                                                   shuffle=True)

                # go through the training set
                if not isinstance(self.rbm_layers[i], GRBM) and epoch == 6:
                    momentum = 0.9

                for mb, minibatch in enumerate(minibatches):
                    current_cost = pretraining_fns[i](indexes=minibatch,
                                                momentum=momentum,
                                                lr=pretrain_lr[i])
                    # iteration number
                    iter = (epoch - 1) * n_train_batches + mb

                    if (iter + 1) % validation_frequency == 0:
                        print('Pre-training cost (layer %i, epoch %d): ' % (i, epoch), end=' ')
                        print(current_cost)

                        # Plot the output
                        if graph_output:
                            plt.clf()
                            training_output = self.get_output(train_set_x, i)
                            plt.imshow(training_output, cmap='gray')
                            plt.axis('tight')
                            plt.title('epoch %d' % (epoch))
                            plt.draw()
                            plt.pause(1.0)

                        # if we got the best validation score until now
                        if current_cost < best_cost:
                            # improve patience if loss improvement is good enough
                            if (
                                    current_cost < best_cost *
                                    improvement_threshold
                            ):
                                patience = max(patience, iter * patience_increase)

                            best_cost = current_cost
                            best_iter = iter

                            if validation_set_x is not None:
                                # Compute the free energy gap
                                if i == 0:
                                    input_t_set = t_set
                                    input_v_set = v_set
                                else:
                                    input_t_set = self.get_output(
                                                    t_set[range(v_set.shape[0])], i-1)
                                    input_v_set = self.get_output(v_set, i-1)

                                free_energy_train, free_energy_test = free_energy_gap_fns[i](
                                                    input_t_set,
                                                    input_v_set)
                                free_energy_gap = free_energy_test.mean() - free_energy_train.mean()

                                print('Free energy gap (layer %i, epoch %i): ' % (i, epoch), end=' ')
                                print(free_energy_gap)

                    if patience <= iter:
                        done_looping = True
                        break

            if graph_output:
                plt.close()

        end_time = timeit.default_timer()


        print('The pretraining code for file ' + os.path.split(__file__)[1] +
              ' ran for %.2fm' % ((end_time - start_time) / 60.), file=sys.stderr)

# batch_size changed from 1 as in M.Liang to 20

def train_MDBN(datafiles,
               datadir='data',
               batch_size=20,
               holdout=0.1,
               repeats=10,
               graph_output=False,
               output_folder='MDBN_run',
               output_file='parameters_and_classes.npz',
               rng=None):
    """
    :param datafile: path to the dataset

    :param batch_size: size of a batch used to train the RBM
    """

    if rng is None:
        rng = numpy.random.RandomState(123)

    #################################
    #     Training the RBM          #
    #################################

    me_DBN, output_ME_t_set, output_ME_v_set = train_ME(datafiles['ME'],
                                                        holdout=holdout,
                                                        repeats=repeats,
                                                        lambda_1=0.01,
                                                        lambda_2=0.01,
                                                        graph_output=graph_output,
                                                        datadir=datadir)

    ge_DBN, output_GE_t_set, output_GE_v_set = train_GE(datafiles['GE'],
                                                        holdout=holdout,
                                                        repeats=repeats,
                                                        lambda_1=0.01,
                                                        lambda_2=0.1,
                                                        graph_output=graph_output,
                                                        datadir=datadir)

    dm_DBN, output_DM_t_set, output_DM_v_set = train_DM(datafiles['DM'],
                                                        holdout=holdout,
                                                        repeats=repeats,
                                                        lambda_1=0.01,
                                                        lambda_2=0.1,
                                                        graph_output=graph_output,
                                                        datadir=datadir)

    print('*** Training on joint layer ***')

    output_ME_t_set, output_ME_v_set = output_DBN(me_DBN,datafiles['ME'],holdout=holdout,repeats=repeats)
    output_GE_t_set, output_GE_v_set = output_DBN(ge_DBN,datafiles['GE'], holdout=holdout, repeats=repeats)
    output_DM_t_set, output_DM_v_set = output_DBN(dm_DBN,datafiles['DM'], holdout=holdout, repeats=repeats)

    joint_train_set = theano.shared(numpy.concatenate([
                    output_ME_t_set, output_GE_t_set, output_DM_t_set],axis=1), borrow=True)

    if holdout > 0:
        joint_val_set = theano.shared(numpy.concatenate([
                            output_ME_v_set, output_GE_v_set, output_DM_v_set],axis=1), borrow=True)
    else:
        joint_val_set = None

    top_DBN = train_top(batch_size, graph_output, joint_train_set, joint_val_set, rng)

    # Identifying the classes

    ME_output, _ = output_DBN(me_DBN,datafiles['ME'])
    GE_output, _ = output_DBN(ge_DBN,datafiles['GE'])
    DM_output, _ = output_DBN(dm_DBN,datafiles['DM'])

    joint_output = theano.shared(numpy.concatenate([ME_output, GE_output, DM_output],axis=1), borrow=True)

    classes = top_DBN.get_output(joint_output)

    save_network(classes, ge_DBN, me_DBN, dm_DBN, top_DBN, holdout, output_file, output_folder, repeats)

    return classes

def save_network(classes, ge_DBN, me_DBN, dm_DBN, top_DBN, holdout, output_file, output_folder, repeats):
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)
    root_dir = os.getcwd()
    os.chdir(output_folder)
    numpy.savez(output_file,
                holdout=holdout,
                repeats=repeats,
                me_config={
                    'number_of_nodes': me_DBN.number_of_nodes(),
                    'epochs': [8000],
                    'learning_rate': [0.005],
                    'batch_size': 20,
                    'k': 10
                },
                ge_config={
                    'number_of_nodes': ge_DBN.number_of_nodes(),
                    'epochs': [8000, 800],
                    'learning_rate': [0.005, 0.1],
                    'batch_size': 20,
                    'k': 1
                },
                dm_config={
                    'number_of_nodes': dm_DBN.number_of_nodes(),
                    'epochs': [8000, 800],
                    'learning_rate': [0.005, 0.1],
                    'batch_size': 20,
                    'k': 1
                },
                top_config={
                    'number_of_nodes': top_DBN.number_of_nodes(),
                    'epochs': [800, 800],
                    'learning_rate': [0.1, 0.1],
                    'batch_size': 20,
                    'k': 1
                },
                classes=classes,
                me_params=[{p.name: p.get_value()} for p in me_DBN.params],
                ge_params=[{p.name: p.get_value()} for p in ge_DBN.params],
                dm_params=[{p.name: p.get_value()} for p in dm_DBN.params],
                top_params=[{p.name: p.get_value()} for p in top_DBN.params]
                )
    os.chdir(root_dir)

def load_network(input_file, input_folder):
    root_dir = os.getcwd()
    # TODO: check if the input_folder exists
    os.chdir(input_folder)
    npz = numpy.load(input_file)

    config = npz['me_config'].tolist()
    params = npz['me_params']
    layer_sizes = config['number_of_nodes']
    me_DBN = DBN(n_ins=layer_sizes[0], hidden_layers_sizes=layer_sizes[1:-1], n_outs=layer_sizes[-1],
                  W_list=[params[0]['W']],b_list=[params[1]['b']])

    config = npz['ge_config'].tolist()
    params = npz['ge_params']
    layer_sizes = config['number_of_nodes']
    ge_DBN = DBN(n_ins=layer_sizes[0], hidden_layers_sizes=layer_sizes[1:-1], n_outs=layer_sizes[-1],
                  W_list=[params[0]['W'],params[2]['W']],b_list=[params[1]['b'],params[3]['b']])

    config = npz['dm_config'].tolist()
    params = npz['dm_params']
    layer_sizes = config['number_of_nodes']
    dm_DBN = DBN(n_ins=layer_sizes[0], hidden_layers_sizes=layer_sizes[1:-1], n_outs=layer_sizes[-1],
                  W_list=[params[0]['W'],params[2]['W']],b_list=[params[1]['b'],params[3]['b']])

    config = npz['top_config'].tolist()
    params = npz['top_params']
    layer_sizes = config['number_of_nodes']
    top_DBN = DBN(n_ins=layer_sizes[0], hidden_layers_sizes=layer_sizes[1:-1], n_outs=layer_sizes[-1],
                  gauss=False,
                  W_list=[params[0]['W'],params[2]['W']],b_list=[params[1]['b'],params[3]['b']])

    os.chdir(root_dir)

    return (me_DBN, ge_DBN, dm_DBN, top_DBN)

def train_top(batch_size, graph_output, joint_train_set, joint_val_set, rng):
    top_DBN = DBN(numpy_rng=rng, n_ins=120,
                  gauss=False,
                  hidden_layers_sizes=[24],
                  n_outs=3)
    top_DBN.pretraining(joint_train_set, joint_val_set,
                        batch_size, k=1,
                        pretraining_epochs=[800, 800],
                        pretrain_lr=[0.1, 0.1],
                        graph_output=graph_output)
    return top_DBN


def train_bottom_layer(train_set, validation_set,
                       batch_size=20,
                       k=1, layers_sizes=[40],
                       pretraining_epochs=[800],
                       pretrain_lr=[0.005],
                       lambda_1 = 0.0,
                       lambda_2 = 0.1,
                       rng=None,
                       graph_output=False
                    ):

    if rng is None:
        rng = numpy.random.RandomState(123)

    print('Visible nodes: %i' % train_set.get_value().shape[1])
    print('Output nodes: %i' % layers_sizes[-1])
    dbn = DBN(numpy_rng=rng, n_ins=train_set.get_value().shape[1],
                  hidden_layers_sizes=layers_sizes[:-1],
                  n_outs=layers_sizes[-1])

    dbn.pretraining(train_set,
                        validation_set,
                        batch_size, k=k,
                        pretraining_epochs=pretraining_epochs,
                        pretrain_lr=pretrain_lr,
                        lambda_1=lambda_1,
                        lambda_2=lambda_2,
                        graph_output=graph_output)

    output_train_set = dbn.get_output(train_set)
    if validation_set is not None:
        output_val_set = dbn.get_output(validation_set)
    else:
        output_val_set = None

    return dbn, output_train_set, output_val_set

def train_DM(datafile,
             clip=None,
             batch_size=20,
             k=1,
             lambda_1=0,
             lambda_2=1,
             layers_sizes=[400, 40],
             pretraining_epochs=[8000, 800],
             pretrain_lr=[0.005, 0.1],
             holdout=0.1,
             repeats=10,
             graph_output=False,
             datadir='data'):
    print('*** Training on DM ***')

    train_set, validation_set = load_n_preprocess_data(datafile,
                                                       clip=clip,
                                                       holdout=holdout,
                                                       repeats=repeats,
#                                                       transform_fn=numpy.power,
#                                                       exponent=1.0/6.0,
                                                       datadir=datadir)

    return train_bottom_layer(train_set, validation_set,
                              batch_size=batch_size,
                              k=k,
                              layers_sizes=layers_sizes,
                              pretraining_epochs=pretraining_epochs,
                              pretrain_lr=pretrain_lr,
                              lambda_1=lambda_1,
                              lambda_2=lambda_2,
                              graph_output=graph_output)

def train_GE(datafile,
             clip=None,
             batch_size=20,
             k=1,
             lambda_1=0,
             lambda_2=1,
             layers_sizes=[400, 40],
             pretraining_epochs=[8000, 800],
             pretrain_lr=[0.005, 0.1],
             holdout=0.1,
             repeats=10,
             graph_output=False,
             datadir='data'):
    print('*** Training on GE ***')

    train_set, validation_set = load_n_preprocess_data(datafile,
                                                       clip=clip,
                                                       holdout=holdout,
                                                       repeats=repeats,
                                                       datadir=datadir)

    return train_bottom_layer(train_set, validation_set,
                              batch_size=batch_size,
                              k=k,
                              layers_sizes=layers_sizes,
                              pretraining_epochs=pretraining_epochs,
                              pretrain_lr=pretrain_lr,
                              lambda_1=lambda_1,
                              lambda_2=lambda_2,
                              graph_output=graph_output)

def train_ME(datafile,
             clip=None,
             batch_size=20,
             k=10,
             lambda_1=0.0,
             lambda_2=0.1,
             layers_sizes=[40],
             pretraining_epochs=[80000],
             pretrain_lr=[0.005],
             holdout=0.1,
             repeats=10,
             graph_output=False,
             datadir='data'):
    print('*** Training on ME ***')

    train_set, validation_set = load_n_preprocess_data(datafile,
                                                       clip=clip,
                                                       holdout=holdout,
                                                       repeats=repeats,
                                                       datadir=datadir)

    return train_bottom_layer(train_set, validation_set,
                                batch_size=batch_size,
                                k=k,
                                layers_sizes=layers_sizes,
                                pretraining_epochs=pretraining_epochs,
                                pretrain_lr=pretrain_lr,
                                lambda_1=lambda_1,
                                lambda_2=lambda_2,
                                graph_output=graph_output)

def output_DBN(dbn,
               datafile,
               holdout=0.0,
               repeats=1,
               clip=None,
               transform_fn=None,
               exponent=1.0,
               datadir='data'):
    train_set, validation_set = load_n_preprocess_data(datafile,
                                          holdout=holdout,
                                          clip=clip,
                                          transform_fn=transform_fn,
                                          exponent=exponent,
                                          repeats=repeats,
                                          shuffle=False,
                                          datadir=datadir)

    return (dbn.get_output(train_set), dbn.get_output(validation_set))


def train_MNIST_Gaussian(graph_output=False):
    # Load the data
    mnist = MNIST()
    raw_dataset = mnist.images
    n_data = raw_dataset.shape[0]

    dataset = mnist.normalize(raw_dataset)

    train_set = theano.shared(dataset[0:int(n_data*5/6)], borrow=True)
    validation_set = theano.shared(dataset[-39:], borrow=True)

    print('*** Training on MNIST ***')

    return train_bottom_layer(train_set, validation_set,
                                batch_size=20,
                                k=1,
                                layers_sizes=[500],
                                pretraining_epochs=[100],
                                pretrain_lr=[0.01],
                                graph_output=graph_output)

def load_n_preprocess_data(datafile,
                           dtype=theano.config.floatX,
                           holdout=0.1,
                           clip=None,
                           transform_fn=None,
                           exponent=1.0,
                           repeats=10,
                           shuffle=True,
                           datadir='data'):
    # Load the data, each column is a single person
    # Pass to a row representation, i.e. the data for each person is now on a
    # single row.
    # Normalize the data so that each measurement on our population has zero
    # mean and zero variance
    n_data, n_cols, data = import_TCGA_data(datafile, datadir, dtype)

    if transform_fn is not None:
        data = transform_fn(data, exponent)

    zdata = stats.zscore(data,axis=1)
    zdata = zdata.T

    if clip is not None:
        zdata = numpy.clip(zdata, clip[0], clip[1])

    # replicate the samples
    if repeats > 1:
        zdata = numpy.repeat(zdata, repeats=repeats, axis=0)

    validation_set_size = int(n_cols*holdout)

    # pre shuffle the data if we have a validation set
    _, indexes = get_minibatches_idx(n_cols, n_cols -
                                     validation_set_size, shuffle = shuffle)

    train_set = theano.shared(zdata[indexes[0]], borrow=True)
    if validation_set_size > 0:
        validation_set = theano.shared(zdata[indexes[1]], borrow=True)
    else:
        validation_set = None

    return train_set, validation_set

if __name__ == '__main__':
    datafiles = prepare_TCGA_datafiles()

    output_dir = 'MDBN_run'
    run_start_date = datetime.datetime.now()
    run_start_date_str = run_start_date.strftime("%Y-%m-%d_%H%M")
    results = []
    for i in range(1):
        dbn_output = train_MDBN(datafiles,
                                output_folder=output_dir,
                                output_file='Exp_%s_run_%d.npz' %
                                                               (run_start_date_str, i),
                                holdout=0.0, repeats=1)
        results.append(find_unique_classes((dbn_output > 0.5) * numpy.ones_like(dbn_output)))

    current_date_time = datetime.datetime.now()
    print('*** Run started at %s' % run_start_date.strftime("%H:%M:%S on %B %d, %Y"))
    print('*** Run completed at %s' % current_date_time.strftime("%H:%M:%S on %B %d, %Y"))

    root_dir = os.getcwd()
    os.chdir(output_dir)
    numpy.savez('Results_%s.npz' % run_start_date_str,
                results=results)
    os.chdir(root_dir)

#    train_RNA(datafiles['mRNA'],graph_output=True)
#    train_GE(datafiles['GE'],graph_output=True)
#    train_MNIST_Gaussian(graph_output=True)
