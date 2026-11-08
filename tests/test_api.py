import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kontur import api as server
from kontur.infrastructure.database import Database


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as folder:
        server._db = Database(f'sqlite:///{Path(folder) / "test.sqlite3"}')
        with TestClient(server.app) as c:
            yield c
        server._db = None


def test_full_demo_flow(client):
    data = client.get('/api/demo').json()
    proposal = client.post('/api/analyze', json=data)
    assert proposal.status_code == 200
    result = proposal.json()
    assert result['plan']['target'] > 0
    assert client.post('/api/backtest', json=data).json()['curve']
    fill = client.post('/api/paper/execute', json={'proposal_id':result['proposal_id']}).json()
    again = client.post('/api/paper/execute', json={'proposal_id':result['proposal_id']}).json()
    assert fill == again
    assert client.get('/api/paper').json()['contracts'] == fill['contracts']
    assert len(client.get('/api/paper').json()['journal']) == 1
    client.post('/api/paper/reset',json={})
    assert client.get('/api/paper').json()['contracts'] == 0
    assert len(client.get('/api/paper').json()['journal']) == 2


def test_stale_and_invalid(client):
    data = client.get('/api/demo').json()
    first = client.post('/api/analyze',json=data).json()
    second = client.post('/api/analyze',json=data).json()
    client.post('/api/paper/execute',json={'proposal_id':first['proposal_id']})
    assert client.post('/api/paper/execute',json={'proposal_id':second['proposal_id']}).status_code == 400
    assert client.post('/api/analyze',json={}).status_code == 400
    assert client.post('/api/live/order',json={}).status_code == 404


def test_demo_rebalance_pause_and_reset(client):
    data=client.get('/api/demo').json()
    first=client.post('/api/analyze',json=data).json()
    assert first['plan']['delta']>0
    client.post('/api/paper/execute',json={'proposal_id':first['proposal_id']})
    data['settings']['margin_buffer']=1
    held=client.post('/api/analyze',json=data).json()
    assert held['plan']['delta']==0
    assert any('Пауза' in w for w in held['plan']['warnings'])
    client.post('/api/paper/reset',json={})
    again=client.post('/api/analyze',json=data).json()
    assert again['plan']['delta']>0


def test_local_only_and_secret(client,monkeypatch):
    monkeypatch.setenv('TINVEST_SANDBOX_TOKEN','private-test-token')
    assert client.get('/api/status').json()['sandbox_configured']
    assert 'private-test-token' not in client.get('/api/status').text
    assert client.get('/api/status',headers={'Host':'evil.test'}).status_code == 403
    assert client.post('/api/paper/reset',json={},headers={'Origin':'https://example.com'}).status_code == 403


def test_sandbox_command_requires_token(client,monkeypatch):
    monkeypatch.delenv('TINVEST_SANDBOX_TOKEN',raising=False)
    payload={'account_id':'test','instrument_id':'uid','quantity':1,'direction':'SELL','command_id':'00000000-0000-4000-8000-000000000001'}
    assert client.post('/api/sandbox/command',json=payload).status_code == 400
