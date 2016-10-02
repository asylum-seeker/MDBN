import numpy as np
import theano
import struct

class MNIST(object):
    def __init__(self, datafile):
        self.n_images = self.load(datafile)

    def load(self, datafile):
        #  http://yann.lecun.com/exdb/mnist/

        # Read the images
        with open(datafile, 'rb') as file:
            data = file.read()
            header = struct.unpack(">IIII", data[:16])
            n_images = header[1]
            print("Reading %i images" % n_images)
            self.sizeY = header[2]
            self.sizeX = header[3]
            print("Images dimension %i x %i" % (self.sizeY, self.sizeX))
            img_size = self.sizeY * self.sizeX
            self.images = np.zeros((n_images,img_size),dtype=theano.config.floatX)
            for i in xrange(n_images):
                image = struct.unpack("B"*img_size, data[16+i*img_size:16+(i+1)*img_size])
                self.images[i] = list(image)

        # Read the labels
        with open('../data/train-labels-idx1-ubyte') as file:
            data = file.read()
            header = struct.unpack(">II", data[:8])
            n_labels = header[1]
            print("Reading %i labels" % n_images)

            self.labels = np.zeros((n_labels,1),dtype=theano.config.floatX)
            for i in xrange(n_labels):
                label = struct.unpack("B", data[8+i:8+i+1])
                self.labels[i] = list(label)

            self.n_levels = np.int(np.max(self.labels)-np.min(self.labels)+1)

        return n_images

    def normalize(self, X):
        # Normalize the images features to have zero mean and approximately unit standard
        # deviation (see https://www.cs.toronto.edu/~hinton/absps/guideTR.pdf 13.2)
        X = (X - 128.0) / 128.0
        # take the global standard deviation as a normalization constant for all features
        gs = np.std(X)
        return X / gs

    def display_weigths(self, X, n_hidden):
        X=X.T
        n_tiles = int(np.round(np.sqrt(n_hidden)))
        img_gap_row = self.sizeY + 1
        img_gap_col = self.sizeX + 1
        Y = np.zeros((n_tiles * img_gap_row, n_tiles * img_gap_col), dtype=theano.config.floatX)
        for r in xrange(n_tiles):
            for c in xrange(n_tiles):
                if (r*n_tiles + c) < n_hidden:
                    Y[r*img_gap_row:(r + 1) * img_gap_row - 1, c * img_gap_col:(c + 1) * img_gap_col - 1] =\
                        X[r * n_tiles + c].reshape(self.sizeX, self.sizeY)
                else:
                    break
        return Y

    def display_samples(self, samples):
        img_gap_row = self.sizeY + 1
        img_gap_col = self.sizeX + 1
        Y = np.zeros((len(samples) * img_gap_row, len(samples[0]) * img_gap_col), dtype=theano.config.floatX)

        for (r, sample) in enumerate(samples):
            for (c, img) in enumerate(sample):
                Y[r*img_gap_row:(r + 1) * img_gap_row - 1, c * img_gap_col:(c + 1) * img_gap_col - 1] =\
                    img.reshape(self.sizeX, self.sizeY)
        return Y