from typing import Iterable, List, Optional

import grpc
from grpc.aio import ClientInterceptor

from t_tech.invest.caching.overrides import (
    InstrumentCacheableMethods,
    InstrumentCacheableMethodsToWarmup,
)
from t_tech.invest.channels import create_channel
from t_tech.invest.constants import INVEST_GRPC_API_SANDBOX
from t_tech.invest.grpc.utils.grpc_services import AsyncServices, Services
from t_tech.invest.typedefs import ChannelArgumentType

__all__ = ("Client", "AsyncClient", "SandboxClient", "AsyncSandboxClient")


class Client:
    """Sync client.

    ```python
    import os
    from t_tech.invest.grpc import Client

    TOKEN = os.environ["INVEST_TOKEN"]

    def main():
        with Client(TOKEN) as client:
            print(client.users.get_accounts())

    ```
    """

    def __init__(
        self,
        token: str,
        *,
        target: Optional[str] = None,
        sandbox_token: Optional[str] = None,
        options: Optional[ChannelArgumentType] = None,
        app_name: Optional[str] = None,
        interceptors: Optional[List[ClientInterceptor]] = None,
        instrument_methods_to_cache: Iterable[InstrumentCacheableMethods] = (  # type: ignore[assignment]
            InstrumentCacheableMethods.SHARES,
            InstrumentCacheableMethods.BONDS,
            InstrumentCacheableMethods.ETFS,
            InstrumentCacheableMethods.FUTURES,
            InstrumentCacheableMethods.CURRENCIES,
            InstrumentCacheableMethods.OPTIONS,
            InstrumentCacheableMethods.SHARE_BY,
            InstrumentCacheableMethods.BOND_BY,
            InstrumentCacheableMethods.ETF_BY,
            InstrumentCacheableMethods.FUTURE_BY,
            InstrumentCacheableMethods.CURRENCY_BY,
            InstrumentCacheableMethods.OPTION_BY,
            InstrumentCacheableMethods.GET_INSTRUMENT_BY,

        ),
        warmup_cache_methods: Iterable[InstrumentCacheableMethodsToWarmup] = tuple(),
    ):
        self._token = token
        self._sandbox_token = sandbox_token
        self._options = options
        self._app_name = app_name
        self._warmup_cache_methods = warmup_cache_methods
        self._instrument_methods_to_cache = instrument_methods_to_cache

        self._channel = create_channel(target=target, options=options)
        if interceptors is None:
            interceptors = []
        for interceptor in interceptors:
            self._channel = grpc.intercept_channel(self._channel, interceptor)

    def __enter__(self) -> Services:
        channel = self._channel.__enter__()
        services = Services(
            channel,
            token=self._token,
            sandbox_token=self._sandbox_token,
            app_name=self._app_name,
            instrument_methods_to_cache=self._instrument_methods_to_cache,
            warmup_cache_methods=self._warmup_cache_methods,
        )
        services.warmup_instruments_cache()
        return services

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._channel.__exit__(exc_type, exc_val, exc_tb)
        return False


class SandboxClient(Client):
    """Sync sandbox client.

    ```python
    import os
    from t_tech.invest.grpc import SandboxClient

    TOKEN = os.environ["INVEST_TOKEN"]

    def main():
        with SandboxClient(TOKEN) as client:
            print(client.users.get_accounts())

    ```
    """

    def __init__(
            self,
            token: str,
            **kwargs,
    ):
        kwargs["target"] = INVEST_GRPC_API_SANDBOX
        super().__init__(token, **kwargs)


class AsyncClient:
    """Async client.

    ```python
    import asyncio
    import os

    from t_tech.invest import AsyncClient

    TOKEN = os.environ["INVEST_TOKEN"]


    async def main():
        async with AsyncClient(TOKEN) as client:
            print(await client.users.get_accounts())


    if __name__ == "__main__":
        asyncio.run(main())
    ```
    """

    def __init__(
        self,
        token: str,
        *,
        target: Optional[str] = None,
        sandbox_token: Optional[str] = None,
        options: Optional[ChannelArgumentType] = None,
        app_name: Optional[str] = None,
        interceptors: Optional[List[ClientInterceptor]] = None,
        instrument_methods_to_cache: Iterable[InstrumentCacheableMethods] = (  # type: ignore[assignment]
            InstrumentCacheableMethods.SHARES,
            InstrumentCacheableMethods.BONDS,
            InstrumentCacheableMethods.ETFS,
            InstrumentCacheableMethods.FUTURES,
            InstrumentCacheableMethods.CURRENCIES,
            InstrumentCacheableMethods.OPTIONS,
            InstrumentCacheableMethods.SHARE_BY,
            InstrumentCacheableMethods.BOND_BY,
            InstrumentCacheableMethods.ETF_BY,
            InstrumentCacheableMethods.FUTURE_BY,
            InstrumentCacheableMethods.CURRENCY_BY,
            InstrumentCacheableMethods.OPTION_BY,
            InstrumentCacheableMethods.GET_INSTRUMENT_BY,

        ),
        warmup_cache_methods: Iterable[InstrumentCacheableMethodsToWarmup] = tuple(),
    ):
        self._token = token
        self._sandbox_token = sandbox_token
        self._options = options
        self._app_name = app_name
        self._channel = create_channel(
            target=target, force_async=True, options=options, interceptors=interceptors
        )
        self._warmup_cache_methods = warmup_cache_methods
        self._instrument_methods_to_cache = instrument_methods_to_cache

    async def __aenter__(self) -> AsyncServices:
        channel = await self._channel.__aenter__()
        services = AsyncServices(
            channel,
            token=self._token,
            sandbox_token=self._sandbox_token,
            app_name=self._app_name,
            instrument_methods_to_cache=self._instrument_methods_to_cache,
            warmup_cache_methods=self._warmup_cache_methods,
        )
        await services.warmup_instruments_cache()
        return services

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._channel.__aexit__(exc_type, exc_val, exc_tb)
        return False


class AsyncSandboxClient(AsyncClient):
    def __init__(
        self,
        token: str,
        **kwargs,
    ):
        kwargs["target"] = INVEST_GRPC_API_SANDBOX
        super().__init__(token, **kwargs)
