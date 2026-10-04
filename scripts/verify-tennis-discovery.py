import asyncio, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.kalshi.client import KalshiReadOnlyClient
from app.services.kalshi.tennis_detector import TennisMarketDetector
async def main():
    c=KalshiReadOnlyClient()
    try:
        if len(sys.argv)>1:
            raw=(await c.get_market(sys.argv[1]))['market']
            print(json.dumps({k:raw.get(k) for k in ('ticker','title','yes_sub_title','no_sub_title','rules_primary')},indent=2))
            print('Normalized:', TennisMarketDetector().players(raw))
            return
        data=await c.search_tennis_markets()
        detector=TennisMarketDetector()
        now=__import__('time').time()*1000
        live=[m for m in data if detector.state(m,now)=='LIVE']
        groups={detector.group_key(m) for m in live if detector.players(m)[1] != 'Opponent unavailable'}
        result={'health':c.discovery_health, 'live_contract_count':len(live),'live_match_count':len(groups),
            'live_matches':[{'ticker':m['ticker'],'players':detector.players(m),'start':m.get('occurrence_datetime')} for m in live]}
        with open(ROOT / 'docs' / 'kalshi-discovery-verification.json','w') as f:
            json.dump(result,f,indent=2)
        print(json.dumps(result,indent=2))
    finally:
        await c.close()
asyncio.run(main())
