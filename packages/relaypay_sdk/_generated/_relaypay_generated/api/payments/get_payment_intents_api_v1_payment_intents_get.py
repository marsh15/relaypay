from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.payment_intent_page import PaymentIntentPage
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    *,
    limit: int | Unset = 25,
    after: None | str | Unset = UNSET,
    merchant_reference: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["Authorization"] = authorization



    

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_after: None | str | Unset
    if isinstance(after, Unset):
        json_after = UNSET
    else:
        json_after = after
    params["after"] = json_after

    json_merchant_reference: None | str | Unset
    if isinstance(merchant_reference, Unset):
        json_merchant_reference = UNSET
    else:
        json_merchant_reference = merchant_reference
    params["merchantReference"] = json_merchant_reference


    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}


    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/payment_intents",
        "params": params,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | PaymentIntentPage | None:
    if response.status_code == 200:
        response_200 = PaymentIntentPage.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | PaymentIntentPage]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 25,
    after: None | str | Unset = UNSET,
    merchant_reference: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | PaymentIntentPage]:
    """ Get Payment Intents

    Args:
        limit (int | Unset):  Default: 25.
        after (None | str | Unset):
        merchant_reference (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PaymentIntentPage]
     """


    kwargs = _get_kwargs(
        limit=limit,
after=after,
merchant_reference=merchant_reference,
authorization=authorization,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 25,
    after: None | str | Unset = UNSET,
    merchant_reference: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,

) -> HTTPValidationError | PaymentIntentPage | None:
    """ Get Payment Intents

    Args:
        limit (int | Unset):  Default: 25.
        after (None | str | Unset):
        merchant_reference (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PaymentIntentPage
     """


    return sync_detailed(
        client=client,
limit=limit,
after=after,
merchant_reference=merchant_reference,
authorization=authorization,

    ).parsed

async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 25,
    after: None | str | Unset = UNSET,
    merchant_reference: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | PaymentIntentPage]:
    """ Get Payment Intents

    Args:
        limit (int | Unset):  Default: 25.
        after (None | str | Unset):
        merchant_reference (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PaymentIntentPage]
     """


    kwargs = _get_kwargs(
        limit=limit,
after=after,
merchant_reference=merchant_reference,
authorization=authorization,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 25,
    after: None | str | Unset = UNSET,
    merchant_reference: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,

) -> HTTPValidationError | PaymentIntentPage | None:
    """ Get Payment Intents

    Args:
        limit (int | Unset):  Default: 25.
        after (None | str | Unset):
        merchant_reference (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PaymentIntentPage
     """


    return (await asyncio_detailed(
        client=client,
limit=limit,
after=after,
merchant_reference=merchant_reference,
authorization=authorization,

    )).parsed
