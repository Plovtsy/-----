"""Explicit read-only smoke test for one dedicated T-Invest Sandbox account."""
import argparse
import json
import os
from kontur.infrastructure.broker import SandboxBroker

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--account-id',required=True,help='Dedicated sandbox account ID')
    parser.add_argument('--future-uid',help='Optional UID found through /api/sandbox/futures')
    args=parser.parse_args()
    if not os.environ.get('TINVEST_SANDBOX_TOKEN'):
        parser.error('TINVEST_SANDBOX_TOKEN is required')
    broker=SandboxBroker()
    accounts=broker.accounts().get('accounts',[])
    if args.account_id not in [a.get('id') for a in accounts]:
        parser.error('Dedicated account ID was not returned by Sandbox')
    portfolio=broker.portfolio(args.account_id)
    positions=broker.positions(args.account_id)
    orders=broker.orders(args.account_id)
    result={'account_id':args.account_id,'accounts':len(accounts),'positions':len(positions.get('futures',[])),
            'active_orders':len(orders.get('orders',[])),'portfolio_received':bool(portfolio)}
    if args.future_uid:
        spec=broker.inspect_future(args.future_uid)
        result['future_uid_verified']=spec.get('future',{}).get('instrument',{}).get('uid')==args.future_uid
    print(json.dumps(result,ensure_ascii=False,indent=2))
