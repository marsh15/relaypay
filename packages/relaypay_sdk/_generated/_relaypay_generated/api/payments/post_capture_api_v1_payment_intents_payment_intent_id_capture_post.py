from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.empty_command import EmptyCommand
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    payment_intent_id: str,
    *,
    body: EmptyCommand,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["Authorization"] = authorization

    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key



    

    

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/payment_intents/{payment_intent_id}/capture".format(payment_intent_id=quote(str(payment_intent_id), safe=""),),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Any | HTTPValidationError | None:
    if response.status_code == 202:
        response_202 = response.json()
        return response_202

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[Any | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    payment_intent_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: EmptyCommand,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,

) -> Response[Any | HTTPValidationError]:
    """ Post Capture

    Args:
        payment_intent_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset):
        body (EmptyCommand):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
     """


    kwargs = _get_kwargs(
        payment_intent_id=payment_intent_id,
body=body,
authorization=authorization,
idempotency_key=idempotency_key,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    payment_intent_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: EmptyCommand,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,

) -> Any | HTTPValidationError | None:
    """ Post Capture

    Args:
        payment_intent_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset):
        body (EmptyCommand):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
     """


    return sync_detailed(
        payment_intent_id=payment_intent_id,
client=client,
body=body,
authorization=authorization,
idempotency_key=idempotency_key,

    ).parsed

async def asyncio_detailed(
    payment_intent_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: EmptyCommand,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,

) -> Response[Any | HTTPValidationError]:
    """ Post Capture

    Args:
        payment_intent_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset):
        body (EmptyCommand):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
     """


    kwargs = _get_kwargs(
        payment_intent_id=payment_intent_id,
body=body,
authorization=authorization,
idempotency_key=idempotency_key,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    payment_intent_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: EmptyCommand,
    authorization: None | str | Unset = UNSET,
    idempotency_key: None | str | Unset = UNSET,

) -> Any | HTTPValidationError | None:
    """ Post Capture

    Args:
        payment_intent_id (str):
        authorization (None | str | Unset):
        idempotency_key (None | str | Unset):
        body (EmptyCommand):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
     """


    return (await asyncio_detailed(
        payment_intent_id=payment_intent_id,
client=client,
body=body,
authorization=authorization,
idempotency_key=idempotency_key,

    )).parsed
