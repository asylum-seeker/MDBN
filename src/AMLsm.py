import os
import sys
import datetime
import json
import numpy
import getopt

from MDBN import train_MDBN

from utils import find_unique_classes

def prepare_AML_TCGA_datafiles(config):

    datafiles = dict()
    for key in config["pathways"]:
        datafiles[key] = config[key]["datafile"]

    return datafiles

def usage():
    print("--help usage summary")
    print("--config=filename configuration file")
    print("--verbose print additional information during training")

def main(argv):
    config_dir = 'config/'
    verbose = False
    config_filename = 'aml_config.json'

    try:
        opts, args = getopt.getopt(argv, "hc:v", ["help", "config=", "verbose"])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            usage()
            sys.exit()
        elif opt in ("-v", "--verbose"):
            verbose = True
        elif opt in ("-c", "--config"):
            config_filename = arg

    with open(config_dir + config_filename) as config_file:
        config = json.load(config_file)

    datafiles = prepare_AML_TCGA_datafiles(config)

    numpy_rng = numpy.random.RandomState(config["seed"])

    results = []
    batch_start_date = datetime.datetime.now()
    batch_start_date_str = batch_start_date.strftime("%Y-%m-%d_%H%M")

    output_dir = 'MDBN_run/AML_Batch_%s' % batch_start_date_str
    os.mkdir(output_dir)

    for i in range(config["runs"]):
        run_start_date = datetime.datetime.now()
        dbn_output = train_MDBN(datafiles,
                                config,
                                output_folder=output_dir,
                                output_file='Exp_%s_run_%d.npz' %
                                            (batch_start_date_str, i),
                                holdout=0.0, repeats=1,
                                verbose=verbose,
                                rng=numpy_rng)
        current_date_time = datetime.datetime.now()
        print('*** Run %i started at %s' % (i, run_start_date.strftime("%H:%M:%S on %B %d, %Y")))
        print('*** Run %i completed at %s' % (i, current_date_time.strftime("%H:%M:%S on %B %d, %Y")))
        classes = find_unique_classes((dbn_output > 0.5) * numpy.ones_like(dbn_output))
        print('*** Identified %d ' % numpy.max(classes[0]))
        results.append(classes[0])

    root_dir = os.getcwd()
    os.chdir(output_dir)
    numpy.savez('Results_%s.npz' % batch_start_date_str,
                results=results)
    os.chdir(root_dir)

if __name__ == '__main__':
    main(sys.argv[1:])