"""Errors. Account and position failures must fail closed (no new orders)."""


class Cos1Error(Exception):
    pass


class LiveEndpointRefused(Cos1Error):
    """The live trading host is never used."""


class FeedRefused(Cos1Error):
    """SIP is refused. Market data is IEX only."""


class BrokerReadError(Cos1Error):
    """A read of account, positions, orders, or fills failed."""


class MissingCredentials(Cos1Error):
    pass


class ConfigError(Cos1Error):
    pass


class AlreadyRunning(Cos1Error):
    pass


class OrderRejected(Cos1Error):
    pass
