#!/usr/bin/python3

import pprint
import sys
import argparse

# ---

import yaml

from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper

import pandas as pd

import dask
import dask.distributed
from dask.distributed import LocalCluster
from dask_jobqueue import SLURMCluster

from dask.distributed import Client

# ---

from recipe import Recipe, EvaluationTask, RawExtractor

from sql_queries import generate_signal_query

# ---

def execute_evaluation_phase(recipe:Recipe, options):
    print(f'execute_evaluation_phase: {recipe}  {recipe.name}')

    op_registry = {}
    op_registry['raw'] = RawExtractor


    if not hasattr(recipe.evaluation, 'extractors'):
        print('execute_evaluation_phase: no `extractors` in recipe.Evaluation')
        return

    print(recipe.evaluation.extractors)
    evaluation = recipe.evaluation

    data_repo = {}
    # setattr(recipe, 'data_repo', data_repo)
    # print(f'{recipe.evaluation.extractors=}')
    for extractor_name in recipe.evaluation.extractors:
        extractor = recipe.evaluation.extractors[extractor_name]

        if extractor_name in options.extraction_overrides:
            extractor.input_files = [ options.extraction_overrides[extractor_name] ]
            print(f'overriding {extractor_name} with {extractor.input_files}')

        delayed_data = extractor.prepare()
        print(f'{extractor=}')
        # print(f'{delayed_data.memory_usage(deep=True) = }')
        print(f'-<-<-<-<-<-<-')
        # extracted.append(delayed_data)
        data_repo[extractor_name] = delayed_data

    if not hasattr(recipe.evaluation, 'transforms'):
        print('execute_evaluation_phase: no `transforms` in recipe.Evaluation')
        return data_repo

    transformed = []
    for transform_name in recipe.evaluation.transforms:
        transform = recipe.evaluation.transforms[transform_name]
        transform.set_data_repo(data_repo)
        transform.execute()
        # transformed.append(transformed_data)

    jobs = []
    for exporter_name in recipe.evaluation.exporter:
        exporter = recipe.evaluation.exporter[exporter_name]

        if exporter_name in options.export_overrides:
            exporter.output_filename = options.export_overrides[exporter_name]
            print(f'overriding {exporter_name} with {exporter.output_filename}')

        exporter.set_data_repo(data_repo)
        job = exporter.execute()
        jobs.append(job)


    print(f'{jobs=}')
    jobs[0].visualize('/opt/tmpssd/t-its-paper/dask_graph_eval.png')

    # now actually compute the constructed computation graph
    dask.compute(*jobs)


    # for task_name in recipe.evaluation.tasks:
    #     task = recipe.evaluation.tasks[task_name]
    #     print('-*='*30)
    #     print(f'{task_name=}')
    #     print(f'{task=}')
    #     pprint.pp(task.__dict__)
    #     print('-*='*30)
    #     task.initialize(op_registry)
    #     print(f'plot: executing evaluation tasks...')
    #     task_result = task.execute(options)
    #     print(f'{task_result=}')


    print('=-!!'*40)


def execute_plotting_phase(recipe:Recipe, options):
    print(f'execute_plotting_phase: {recipe}  {recipe.name}')

    data_repo = {}

    for dataset_name in recipe.plot.reader:
        reader = recipe.plot.reader[dataset_name]
        print(f'plot: loading dataset: "{dataset_name=}"')
        if dataset_name in options.reader_overrides:
            reader.input_files = options.reader_overrides[dataset_name]
            print(f'plot: execute_plotting_phase overriding input files for "{dataset_name}": "{reader.input_files=}"')
        data = reader.read_data()
        data_repo[dataset_name] = data

    print('<<<-<-<--<-<-<--<-<-<')
    print(f'plot: {data_repo=}')
    print('<<<-<-<--<-<-<--<-<-<')

    for task_name in recipe.plot.transforms:
        task = recipe.plot.transforms[task_name]
        task.set_data_repo(data_repo)
        task.execute()

    jobs = []
    for task_name in recipe.plot.tasks:
        task = recipe.plot.tasks[task_name]
        print(f'plot: {task_name=}')
        print(f'plot: {task=}')
        print(f'plot: {task.dataset_name=}')
        # print(f'plot: loading data...')
        # task.load_data()
        task.set_data_repo(data_repo)
        print(f'plot: executing plotting tasks...')
        job = task.execute()
        # print(f'plot: {job=}')
        jobs.append(job)

    jobs[0].visualize('/opt/tmpssd/t-its-paper/dask_graph_plot.png')
    print(f'plot: {jobs=}')
    r = dask.compute(*jobs)
    print(f'plot: {r=}')


def process_recipe(options):
    f = open(options.recipe, mode='r')

    # recipe = load(f.read(), Loader=Loader)
    recipe = yaml.unsafe_load(f.read())

    pprint.pp(recipe)
    pprint.pp(recipe.__dict__)


    output = dump(recipe, Dumper=Dumper)

    if not hasattr(recipe, 'evaluation'):
        print('process_recipe: no Evaluation in recipe')
        return

    if not options.plot_only:
        execute_evaluation_phase(recipe, options)

    if options.eval_only:
        return

    execute_plotting_phase(recipe, options)


def extract_dict_from_string(string):
    d = dict()
    for token in string.split(','):
        key, value = token.strip(',').split(':')
        d[key] = value
    return d


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('recipe', help='input recipe')

    parser.add_argument('--override-extractor', type=str, help='override extractor parameters')
    parser.add_argument('--override-exporter', type=str, help='override exporter parameters')

    parser.add_argument('--override-reader', type=str, help='override reader parameters')

    parser.add_argument('--eval-only', action='store_true', default=False, help='run eval phase only')
    parser.add_argument('--plot-only', action='store_true', default=False, help='run plot phase only')

    parser.add_argument('--worker', type=int, help='the number of worker processes')

    parser.add_argument('--slurm', action='store_true', default=False, help='use SLURM cluster')
    parser.add_argument('--nodelist', type=str, help='nodelist for SLURM')

    args = parser.parse_args()

    if args.slurm:
        if not args.nodelist:
            raise Exception('A nodelist ist required when using SLURM')

    def set_dict_arg_from_string(option, arg_name):
        if option:
            option_dict = extract_dict_from_string(option)
            print(f'{option_dict=}')
            setattr(args, arg_name, option_dict)
        else:
            setattr(args, arg_name, dict())

    set_dict_arg_from_string(args.override_extractor, 'extraction_overrides')
    set_dict_arg_from_string(args.override_exporter, 'export_overrides')

    set_dict_arg_from_string(args.override_reader, 'reader_overrides')

    return args


def setup(options):
    # verbose printing of DataFrames
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_colwidth', None)

    if options.slurm:
        print('using SLURM cluster')
        cluster = SLURMCluster(cores = 1
                             , n_workers = options.worker
                             # , n_workers = 1
                             # , processes = options.worker
                             # , processes = 1
                             , memory = "1GB"
                             , account = "dask_test"
                             # , queue = "normal"
                             , job_extra_directives = [ f'--nodelist={options.nodelist}' ]
                             , interface = 'lo'
                             , shared_temp_directory = '/opt/tmpssd/t-its-paper/tmp/'
                             )
    else:
        print('using local cluster')
        cluster = LocalCluster(n_workers=options.worker
                             , host='localhost'
                             # , interface='lo'
                             )

    # client = Client('tcp://127.0.0.1:33745')


def main():
    options = parse_args()
    print(f'{options=}')

    setup(options)

    process_recipe(options)
    print(globals())




if __name__=='__main__':
    main()
