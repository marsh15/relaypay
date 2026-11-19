from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Unset
from typing import cast
import datetime



def _get_kwargs(
    environment_id: str,
    *,
    source_reference: str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_csrf_token, Unset):
        headers["X-CSRF-Token"] = x_csrf_token

    if not isinstance(authorization, Unset):
        headers["Authorization"] = authorization



    cookies = {}
    if relaypay_session is not UNSET:
        cookies["relaypay_session"] = relaypay_session



    params: dict[str, Any] = {}

    params["sourceReference"] = source_reference

    json_period_start = period_start.isoformat()
    params["periodStart"] = json_period_start

    json_period_end = period_end.isoformat()
    params["periodEnd"] = json_period_end


    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}


    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/admin/v1/environments/{environment_id}/statement-imports/stream".format(environment_id=quote(str(environment_id), safe=""),),
        "params": params,
        "cookies": cookies,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Any | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = response.json()
        return response_200

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
    environment_id: str,
    *,
    client: AuthenticatedClient | Client,
    source_reference: str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[Any | HTTPValidationError]:
    """ Post Streamed Statement Import

     Import a CSV statement of any size: the body is streamed, never held in memory.

    Args:
        environment_id (str):
        source_reference (str):
        period_start (datetime.datetime):
        period_end (datetime.datetime):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
source_reference=source_reference,
period_start=period_start,
period_end=period_end,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    environment_id: str,
    *,
    client: AuthenticatedClient | Client,
    source_reference: str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Any | HTTPValidationError | None:
    """ Post Streamed Statement Import

     Import a CSV statement of any size: the body is streamed, never held in memory.

    Args:
        environment_id (str):
        source_reference (str):
        period_start (datetime.datetime):
        period_end (datetime.datetime):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
     """


    return sync_detailed(
        environment_id=environment_id,
client=client,
source_reference=source_reference,
period_start=period_start,
period_end=period_end,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    *,
    client: AuthenticatedClient | Client,
    source_reference: str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[Any | HTTPValidationError]:
    """ Post Streamed Statement Import

     Import a CSV statement of any size: the body is streamed, never held in memory.

    Args:
        environment_id (str):
        source_reference (str):
        period_start (datetime.datetime):
        period_end (datetime.datetime):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
source_reference=source_reference,
period_start=period_start,
period_end=period_end,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    environment_id: str,
    *,
    client: AuthenticatedClient | Client,
    source_reference: str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Any | HTTPValidationError | None:
    """ Post Streamed Statement Import

     Import a CSV statement of any size: the body is streamed, never held in memory.

    Args:
        environment_id (str):
        source_reference (str):
        period_start (datetime.datetime):
        period_end (datetime.datetime):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
client=client,
source_reference=source_reference,
period_start=period_start,
period_end=period_end,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
