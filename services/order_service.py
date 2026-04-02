import time
import logging
from kiteconnect import KiteConnect
from services.trading_state import PositionInfo
from services.strategy import Signal

logger = logging.getLogger(__name__)

MAX_POLL_ATTEMPTS = 15   # seconds to wait for order completion


def place_entry_order(kite: KiteConnect, instrument: dict, signal: Signal) -> str:
    """
    Place a MARKET BUY order for 1 lot of the ATM option (MIS product).
    Returns order_id.
    """
    qty = int(instrument.get("lot_size", 75))
    tradingsymbol = instrument["tradingsymbol"]
    exchange = instrument["exchange"]

    logger.info(
        "Placing entry order | %s %s | qty=%d | signal=%s",
        exchange, tradingsymbol, qty, signal.value
    )

    order_id = kite.place_order(
        variety=kite.VARIETY_REGULAR,
        exchange=exchange,
        tradingsymbol=tradingsymbol,
        transaction_type=kite.TRANSACTION_TYPE_BUY,
        quantity=qty,
        product=kite.PRODUCT_MIS,
        order_type=kite.ORDER_TYPE_MARKET,
    )

    logger.info("Entry order placed | order_id=%s", order_id)
    return str(order_id)


def get_average_price(kite: KiteConnect, order_id: str) -> float:
    """
    Poll order history until status == COMPLETE.
    Returns average fill price.
    Raises TimeoutError if not filled within MAX_POLL_ATTEMPTS seconds.
    """
    for attempt in range(MAX_POLL_ATTEMPTS):
        history = kite.order_history(order_id)
        latest = history[-1] if history else {}
        status = latest.get("status", "")

        if status == "COMPLETE":
            avg = float(latest.get("average_price", 0))
            logger.info("Order %s filled | avg_price=%.2f", order_id, avg)
            return avg

        if status in ("REJECTED", "CANCELLED"):
            raise RuntimeError(
                f"Order {order_id} was {status}: {latest.get('status_message', '')}"
            )

        logger.debug("Waiting for order %s | status=%s | attempt=%d", order_id, status, attempt + 1)
        time.sleep(1)

    raise TimeoutError(f"Order {order_id} did not complete in {MAX_POLL_ATTEMPTS}s")


def place_exit_order(kite: KiteConnect, position: PositionInfo, reason: str) -> str:
    """
    Place a MARKET SELL order to close the open position.
    Returns exit order_id.
    """
    logger.info(
        "Placing exit order | %s | qty=%d | reason=%s",
        position.option_symbol, position.qty, reason
    )

    order_id = kite.place_order(
        variety=kite.VARIETY_REGULAR,
        exchange="NFO",
        tradingsymbol=position.option_symbol,
        transaction_type=kite.TRANSACTION_TYPE_SELL,
        quantity=position.qty,
        product=kite.PRODUCT_MIS,
        order_type=kite.ORDER_TYPE_MARKET,
    )

    logger.info("Exit order placed | order_id=%s | reason=%s", order_id, reason)
    return str(order_id)


def verify_position_exists(kite: KiteConnect, tradingsymbol: str) -> bool:
    """
    Check that the symbol has a non-zero net quantity in today's positions.
    Prevents double-exit if position was already closed externally.
    """
    try:
        positions = kite.positions()
        day_positions = positions.get("day", [])
        for pos in day_positions:
            if pos["tradingsymbol"] == tradingsymbol and pos["quantity"] != 0:
                return True
        return False
    except Exception as e:
        logger.warning("Could not verify position for %s: %s", tradingsymbol, e)
        return True  # Assume exists on error — safer to attempt exit
