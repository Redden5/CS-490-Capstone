"""Run reconstructed-schema tests in a disposable local PostgreSQL 17 container.

No existing containers, volumes, credentials or Supabase connections are used.
"""
import json
import argparse
from pathlib import Path
import subprocess
import time
import unittest
import uuid

from backend.tests.run_reconstructed_staging_tests import ReconstructedTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-rpc', action='store_true', help='Also test optional migration 002 and Supabase RPC SQL')
    args = parser.parse_args()
    import psycopg
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    def docker(*args, check=True):
        return subprocess.run(['docker', *args], capture_output=True, text=True,
                              check=check, creationflags=flags)
    context = json.loads(docker('context', 'inspect').stdout)[0]
    if not context['Endpoints']['docker']['Host'].startswith('npipe://'):
        raise RuntimeError('Expected local Docker Desktop named-pipe context; remote Docker is prohibited')
    name = 'capstone-prerequisite-pg17-' + uuid.uuid4().hex[:12]
    result = None
    evidence = {'container_name': name, 'image': 'postgres:17', 'schema_kind': 'MANUAL_RECONSTRUCTION_NOT_PG_DUMP'}
    try:
        print('Starting disposable PostgreSQL 17 Docker container...', flush=True)
        docker('run', '--detach', '--name', name, '--publish', '127.0.0.1::5432',
               '--env', 'POSTGRES_USER=pipeline_test', '--env', 'POSTGRES_PASSWORD=ephemeral-local-test',
               '--env', 'POSTGRES_DB=capstone_prerequisite_test',
               '--tmpfs', '/var/lib/postgresql/data', 'postgres:17')
        info = json.loads(docker('inspect', name).stdout)[0]
        port = info['NetworkSettings']['Ports']['5432/tcp'][0]
        if port['HostIp'] != '127.0.0.1':
            raise RuntimeError('Container database must bind only to loopback')
        evidence['image_id'] = info['Image']
        dsn = psycopg.conninfo.make_conninfo(host='127.0.0.1', port=port['HostPort'],
                    user='pipeline_test', password='ephemeral-local-test',
                    dbname='capstone_prerequisite_test', sslmode='disable', gssencmode='disable', connect_timeout=2)
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(dsn, autocommit=True) as connection:
                    version = connection.execute('SHOW server_version').fetchone()[0]
                    if connection.info.server_version // 10000 != 17:
                        raise RuntimeError('Expected PostgreSQL major version 17')
                    evidence['server_version'] = version
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Disposable container did not become ready') from None
                time.sleep(0.5)
        print('Testing reconstructed schema on PostgreSQL ' + version, flush=True)
        ReconstructedTests.dsn = dsn
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReconstructedTests)
        if args.include_rpc:
            from backend.tests.run_supabase_rpc_staging_tests import RPCStagingTests
            RPCStagingTests.dsn = dsn
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(RPCStagingTests))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        evidence.update(tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped))
    finally:
        exists = docker('container', 'inspect', name, check=False)
        if exists.returncode == 0:
            docker('rm', '--force', '--volumes', name)
        elif 'No such' not in exists.stderr:
            raise RuntimeError('Unable to inspect temporary container for cleanup')
        absent = docker('container', 'inspect', name, check=False)
        evidence['container_removed'] = absent.returncode != 0 and 'No such' in absent.stderr
        if not evidence['container_removed']:
            raise RuntimeError('Temporary container cleanup could not be verified')
        filename = 'SUPABASE_RPC_STAGING_RESULTS.json' if args.include_rpc else 'DOCKER_POSTGRES17_RESULTS.json'
        output = Path(__file__).resolve().parents[1] / 'scripts/data/prerequisites/investigation' / filename
        output.write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')
        print('Temporary container and its storage removed.', flush=True)
    return 0 if result and result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
