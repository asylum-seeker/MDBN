import numpy as np
import theano
from theano import tensor as T
from theano.tensor.shared_randomstreams import RandomStreams
import struct

class GRBM(object):
    def __init__(self, n_visible, n_hidden, input):
        self.n_visible = n_visible
        self.n_hidden = n_hidden
        self.input = input

        # Rescale terms for visible units
        self.a = theano.shared(value=np.zeros((n_visible,1),dtype=theano.config.floatX),
                               name='a')
        # Bias terms for hidden units
        self.b = theano.shared(np.zeros((n_hidden,1),dtype=theano.config.floatX),
                               name='b')
        # Rescale terms for visible units
        self.sigma = theano.shared(np.ones((n_hidden,1),dtype=theano.config.floatX),
                               name='sigma')
        # Weights
        rng = np.random.RandomState(2468)
        self.W = theano.shared(np.asarray(
                    rng.uniform(
                            -4 * np.sqrt(6. / (n_hidden + n_visible)),
                            4 * np.sqrt(6. / (n_hidden + n_visible)),
                            (n_visible, n_hidden)
                        ),dtype=theano.config.floatX),
                    name='W')
        self.srng = RandomStreams(rng.randint(2 ** 30))

    def v_sample(self, h):
        # Derive a sample of visible units from the hidden units h
        mu = self.b + self.sigma * T.dot(h,self.W)
        return self.srng.normal(size=mu.shape,avg=mu, std=self.sigma, dtype=theano.config.floatX)

    def h_sample(self, v):
        # Derive a sample of hidden units from the visible units v
        activation = T.dot(v,self.W.T) + self.a
        prob = T.nnet.sigmoid(activation)
        return self.srng.binomial(size=activation.shape,n=1,p=prob,dtype=theano.config.floatX)

    def gibbs_update(self, h):
        # Negative phase
        nv_sample = self.v_sample(h)
        nh_sample = self.h_sample(nv_sample)
        return [nv_sample, nh_sample]

    def CD(self, k=1, eps=0.1):
        # Positive phase
        h0_sample = self.h_sample(self.input)

        # Negative phase
        ( [ nv_samples,
            nh_samples],
          updates) = theano.scan(self.gibbs_update,outputs_info=[None, h0_sample],n_steps=k,name="gibbs_update")

        vK_sample = nv_samples[-1]
        hK_sample = nh_samples[-1]

        w_grad = T.dot(self.input.T, h0_sample.T) - T.dot(vK_sample.T, hK_sample.T)

        a_grad = self.input - vK_sample

        b_grad = h0_sample - hK_sample

        params = [self.W, self.a, self.b]
        gparams = [w_grad, a_grad, b_grad]

        for param, gparam in zip(params, gparams):
            updates[param] = param + T.cast(eps,dtype=theano.config.floatX)*gparam

        return updates

def load_MNIST():
    #  http://yann.lecun.com/exdb/mnist/

    # Read the images
    with open('../data/train-images-idx3-ubyte', 'rb') as file:
        data = file.read()
        header = struct.unpack(">IIII", data[:16])
        n_images = header[1]
        print("Reading %i images" % n_images)
        n_row = header[2]
        n_col = header[3]
        print("Images dimension %i x %i" % (n_row, n_col))
        img_size = n_row * n_col
        images = np.zeros((n_images,img_size),dtype=theano.config.floatX)
        for i in xrange(n_images):
            image = struct.unpack("B"*img_size, data[16+i*img_size:16+(i+1)*img_size])
            images[i] = list(image)

    # Read the labels
    with open('../data/train-labels-idx1-ubyte') as file:
        data = file.read()
        header = struct.unpack(">II", data[:8])
        n_labels = header[1]
        print("Reading %i labels" % n_images)

        labels = np.zeros((n_labels,1),dtype=theano.config.floatX)
        for i in xrange(n_labels):
            label = struct.unpack("B", data[8+i:8+i+1])
            labels[i] = list(label)

        n_levels = np.int(np.max(labels)-np.min(labels)+1)

    # print("Data example")
    # print(np.reshape(images[0],(n_row,n_col)))
    # print(labels[0])

    return [n_images, img_size, images, n_levels, labels]

def test():
    n_data, input_size, dataset, levels, targets = load_MNIST()

    index = T.lscalar('index')
    x = T.matrix('x')
    print("Building an RPM with %i visible inputs and %i hidden units" % (input_size, levels))
    rbm = GRBM(input_size, levels, x)

    updates = rbm.CD()

    train_set = theano.shared(dataset)

    train = theano.function(
        inputs=[index],
        updates=updates,
        givens={
            x: train_set[index*20 : (index+1)*20]
        },
        name="train"
    )

    for i in xrange(10):
        for n_batch in xrange(n_data//20):
            train(n_batch)

    print(rbm.a)
    print(rbm.b)
    
if __name__ == '__main__':
    test()