"""The focused walletAgeDays API: two inputs, one integer on success."""


def walletAgeDays(blockchain, address):
    """Return completed days since the earliest known on-chain observation.

    Raises WalletAgeError for invalid, unobserved or unavailable lookups. EVM
    aliases share the explicitly enabled chain coverage, currently Ethereum.
    """
    from .factory import default_service
    from .service import validate
    validate(blockchain, address)
    return default_service().walletAgeDays(blockchain, address)


__all__ = ["walletAgeDays"]
