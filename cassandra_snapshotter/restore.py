from __future__ import absolute_import

import os
import sys
import argparse
import subprocess
import shutil
import time

from cass_functions import (cassandra_query, get_data_dir, get_keyspaces,
                            get_table_directories, get_dir_structure)
from cleaner import data_cleaner


def parse_cmd():

    parser = argparse.ArgumentParser(description='Snapshotter')

    parser.add_argument('-d', '--path', 
                        type=check_dir, 
                        required=True,
                        help="Specify path to load snapshots"
    )
    parser.add_argument('-n', '--node', '--host',
                        required=True,
                        nargs='+',
                        help="Specify host address(es)"
    ) # TODO need to test on multinode cluster, necessary?
      # script running on single nodes or data center?
    parser.add_argument('-k', '-ks', '--keyspace',
                        required=False,
                        nargs='+',
                        help="Specify keyspace(s)"
    )
    parser.add_argument('-tb', '-t', '--table', '-cf', '--column_family',
                        required=False,
                        nargs='+',
                        help="Specify table(s)"
    )
    parser.add_argument('-y',
                        required=False,
                        action='store_true',
                        help="Destroy existing database without prompt"
    )
    # TODO sstableloader option? safer loading?

    return parser.parse_args()


def check_dir(folder):

    if not os.path.isdir(folder):
        raise argparse.ArgumentTypeError('Directory does not exist')
    if os.access(folder, os.R_OK):
        return folder
    else:
        raise argparse.ArgumentTypeError('Directory is not readable')


def restore_schema(host, load_path, keyspace):
    # Cassandra should only need one host in the cluster to restore the schema
    # in every node

    schema_location = load_path + '/' + keyspace + '/' + keyspace + '_schema.cql'

    if not os.path.exists(schema_location):
        raise Exception('Schema not found: %s' % schema_location)

    with open(schema_location, 'r') as f:
        cassandra_query(host, f.read())


def clean_directory(table_directory):
    # TODO does incremental backups work with this?
    for f in os.listdir(table_directory):
        if f.endswith('.db') or f.endswith('.txt') or f.endswith('.crc32'):
            os.remove(table_directory + '/' + f)


def destroy_schema(host, flag=None):

    success = False
    destroy = False
    keyspaces = get_keyspaces(host)

    if len(keyspaces) > 0:

        print('Removing keyspaces:')
        for k in keyspaces:
            print('\t' + k)
        if not flag:

            option = raw_input('Destroy keyspaces? [y/n]')
            if option == 'y' or option == 'Y':
                destroy = True

        elif flag == '-y':
            destroy = True

        else: # should never happen
            raise Exception('Invalid flag parameter')

        if destroy:

            for k in keyspaces: # drop old keyspaces
                print('Dropping keyspace: %s' % k)
                cassandra_query(host, 'DROP KEYSPACE %s;' % k)

            data_dir = get_data_dir()
            active_dirs = os.listdir(data_dir)

            print('Removing old keyspace directories')
            for d in active_dirs:
                if d in keyspaces:
                    print('Removing keyspace directory: %s/%s' % (data_dir, d))
                    shutil.rmtree(data_dir + '/' + d)
                
            success = True

    else:
        success = True

    return success


def restore(hosts, load_path, keyspace_arg = None, table_arg = None,
            y_flag=None):

    print('Destroying existing database')
    if not destroy_schema(hosts[0], y_flag):
        print('Unable to destroy previous data, exiting script')
        sys.exit(0)
    data_cleaner(hosts[0])

    # keyspaces inside snapshot directory
    avaliable_keyspaces = filter(lambda x: os.path.isdir(load_path + '/' + x), \
                                 os.listdir(load_path))

    print('Checking keyspace arguments')
    if keyspace_arg:

        for keyspace in keyspace_arg:
            if keyspace not in avaliable_keyspaces:
                raise Exception('Keyspace "%s" not in snapshot folder' % keyspace)
        load_keyspaces = keyspace_arg

    else:
        load_keyspaces = avaliable_keyspaces

    for keyspace in load_keyspaces:
        print('Creating schema for %s' % keyspace)
        restore_schema(hosts[0], load_path, keyspace)

    existing_keyspaces = get_keyspaces(hosts[0])
    structure = get_dir_structure(hosts[0], existing_keyspaces)

    for keyspace in load_keyspaces:

        print('Loading keyspaces %s' % keyspace)
        load_tables = filter(
                lambda x: os.path.isdir(load_path + '/' + keyspace + '/' + x),
                os.listdir(load_path + '/' + keyspace)
        )
        existing_tables = structure[keyspace].keys()

        for table in load_tables:

            if table not in existing_tables:
                raise Exception('Table not in schema, error with snapshot')

            load_table_dir = load_path + '/' + keyspace + '/' + table
            print('\n\nLoading table: %s' % table)
            subprocess.call(['/bin/sstableloader',
                             '-d', ', '.join(hosts),
                             load_table_dir])

    print('Restoration complete')


if __name__ == '__main__':

    cmds = parse_cmd()
    if cmds.path.endswith('\\') or cmds.path.endswith('/'):
        load_path = cmds.path[:-1]
    else:
        load_path = cmds.path

    if len(cmds.node) == 0:
        raise Exception('Node/host ip required. See restore.py -h for details.')

    start = time.time()
    restore(cmds.node, load_path, cmds.keyspace, cmds.table, cmds.y)
    end = time.time()

    print('Elapsed time: %s' % (end - start))

