"""TEMPLATE — copy this to build a strategy. The runner looks for `STRATEGY`.

Implement ENTRY alpha in on_bar(); the engine owns exits via self.risk.
Return "LONG", "SHORT", or None. Entries fill at the NEXT bar's open.
Never read ctx arrays past ctx.i (look-ahead). See engine.py header for the
full BarContext field list.
"""
from engine import RiskParams


class Strategy:
    name = "template"
    risk = RiskParams(sl_pts=30, target_pts=60, trail_trigger_pts=40,
                      trail_gap_pts=20, breakeven_pts=15, time_stop_bars=12,
                      max_trades_per_day=2)

    def on_session_start(self, ctx):
        pass

    def on_bar(self, ctx):
        return None


STRATEGY = Strategy()
