"""Explicit, repeatable import of the legacy demo ledger into PostgreSQL.

Run after alembic upgrade head. Creates a consistent SQLite backup before writes.
Existing target rows are never overwritten.
"""
import argparse
import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from kontur.infrastructure.database import Account, Database, Event, Proposal


def migrate(source: Path, target: Database, apply: bool = False, allow_sqlite: bool = False):
    if target.engine.dialect.name != 'postgresql' and not (allow_sqlite and target.engine.dialect.name == 'sqlite'):
        raise ValueError('Migration target must be PostgreSQL')
    if not source.is_file():
        raise FileNotFoundError(source)
    backup = source.with_name(source.name + '.backup-' + time.strftime('%Y%m%d-%H%M%S'))
    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row
    with sqlite3.connect(backup) as dst:
        src.backup(dst)
    old_account = src.execute('SELECT * FROM account WHERE id=1').fetchone()
    old_proposals = src.execute('SELECT * FROM proposals ORDER BY created').fetchall()
    old_events = src.execute('SELECT * FROM journal ORDER BY id').fetchall()
    summary = {'backup':str(backup),'source_proposals':len(old_proposals),'source_events':len(old_events),'applied':apply}
    if not apply:
        src.close()
        return summary
    with Session(target.engine) as db:
        a=db.get(Account,'demo:local')
        existing_proposals=db.scalar(select(func.count()).select_from(Proposal).where(Proposal.account_key=='demo:local'))
        existing_events=db.scalar(select(func.count()).select_from(Event).where(Event.account_key=='demo:local'))
        if a is not None and (a.revision != 0 or a.contracts != 0 or a.cash is not None or a.context is not None or existing_proposals or existing_events):
            raise RuntimeError('Target demo account already contains data; refusing to overwrite')
        if a is None:
            a=Account(key='demo:local',mode='demo',halted=False,reconciled=False)
            db.add(a)
        a.revision=old_account['revision']
        a.contracts=old_account['contracts']
        a.cash=Decimal(str(old_account['cash'])) if old_account['cash'] is not None else None
        a.context=old_account['context']
        db.flush()
        for p in old_proposals:
            db.add(Proposal(id=p['id'],account_key=a.key,created=Decimal(str(p['created'])),revision=p['revision'],
                            context=p['context'],payload=json.loads(p['payload']),result=json.loads(p['result']),
                            executed=json.loads(p['executed']) if p['executed'] else None))
        for e in old_events:
            db.add(Event(account_key=a.key,created=Decimal(str(e['created'])),kind=e['kind'],detail=json.loads(e['detail'])))
        db.commit()
        summary['target_proposals']=db.scalar(select(func.count()).select_from(Proposal).where(Proposal.account_key==a.key))
        summary['target_events']=db.scalar(select(func.count()).select_from(Event).where(Event.account_key==a.key))
    src.close()
    if summary['source_proposals']!=summary['target_proposals'] or summary['source_events']!=summary['target_events']:
        raise RuntimeError('Row counts differ after migration')
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path('data/paper.sqlite3'))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--apply-local',action='store_true',help='Import into local SQLAlchemy SQLite for standalone demo')
    args=parser.parse_args()
    print(json.dumps(migrate(args.source,Database(),args.apply or args.apply_local,args.apply_local),ensure_ascii=False,indent=2))
