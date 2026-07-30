from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from typing import cast



def _get_kwargs(
    connector_id: str,
    *,
    x_provider_event_id: str,
    x_provider_timestamp: str,
    x_provider_signature: str,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["X-Provider-Event-ID"] = x_provider_event_id

    headers["X-Provider-Timestamp"] = x_provider_timestamp

    headers["X-Provider-Signature"] = x_provider_signature



    

    

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/inbound/v1/connectors/{connector_id}/events".format(connector_id=quote(str(connector_id), safe=""),),
    }


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
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_provider_event_id: str,
    x_provider_timestamp: str,
    x_provider_signature: str,

) -> Response[Any | HTTPValidationError]:
    """ Post Event

    Args:
        connector_id (str):
        x_provider_event_id (str):
        x_provider_timestamp (str):
        x_provider_signature (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
     """


    kwargs = _get_kwargs(
        connector_id=connector_id,
x_provider_event_id=x_provider_event_id,
x_provider_timestamp=x_provider_timestamp,
x_provider_signature=x_provider_signature,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_provider_event_id: str,
    x_provider_timestamp: str,
    x_provider_signature: str,

) -> Any | HTTPValidationError | None:
    """ Post Event

    Args:
        connector_id (str):
        x_provider_event_id (str):
        x_provider_timestamp (str):
        x_provider_signature (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
     """


    return sync_detailed(
        connector_id=connector_id,
client=client,
x_provider_event_id=x_provider_event_id,
x_provider_timestamp=x_provider_timestamp,
x_provider_signature=x_provider_signature,

    ).parsed

async def asyncio_detailed(
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_provider_event_id: str,
    x_provider_timestamp: str,
    x_provider_signature: str,

) -> Response[Any | HTTPValidationError]:
    """ Post Event

    Args:
        connector_id (str):
        x_provider_event_id (str):
        x_provider_timestamp (str):
        x_provider_signature (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
     """


    kwargs = _get_kwargs(
        connector_id=connector_id,
x_provider_event_id=x_provider_event_id,
x_provider_timestamp=x_provider_timestamp,
x_provider_signature=x_provider_signature,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    connector_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_provider_event_id: str,
    x_provider_timestamp: str,
    x_provider_signature: str,

) -> Any | HTTPValidationError | None:
    """ Post Event

    Args:
        connector_id (str):
        x_provider_event_id (str):
        x_provider_timestamp (str):
        x_provider_signature (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
     """


    return (await asyncio_detailed(
        connector_id=connector_id,
client=client,
x_provider_event_id=x_provider_event_id,
x_provider_timestamp=x_provider_timestamp,
x_provider_signature=x_provider_signature,

    )).parsed
