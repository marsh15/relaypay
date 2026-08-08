from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.get_disputes_api_admin_v1_environments_environment_id_disputes_get_response_200_item import GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    environment_id: str,
    *,
    limit: int | Unset = 50,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["Authorization"] = authorization



    cookies = {}
    if relaypay_session is not UNSET:
        cookies["relaypay_session"] = relaypay_session



    params: dict[str, Any] = {}

    params["limit"] = limit


    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}


    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/admin/v1/environments/{environment_id}/disputes".format(environment_id=quote(str(environment_id), safe=""),),
        "params": params,
        "cookies": cookies,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in (_response_200):
            response_200_item = GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item.from_dict(response_200_item_data)



            response_200.append(response_200_item)

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item]]:
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
    limit: int | Unset = 50,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item]]:
    """ Get Disputes

    Args:
        environment_id (str):
        limit (int | Unset):  Default: 50.
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item]]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
limit=limit,
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
    limit: int | Unset = 50,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item] | None:
    """ Get Disputes

    Args:
        environment_id (str):
        limit (int | Unset):  Default: 50.
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item]
     """


    return sync_detailed(
        environment_id=environment_id,
client=client,
limit=limit,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item]]:
    """ Get Disputes

    Args:
        environment_id (str):
        limit (int | Unset):  Default: 50.
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item]]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
limit=limit,
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
    limit: int | Unset = 50,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item] | None:
    """ Get Disputes

    Args:
        environment_id (str):
        limit (int | Unset):  Default: 50.
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[GetDisputesApiAdminV1EnvironmentsEnvironmentIdDisputesGetResponse200Item]
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
client=client,
limit=limit,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
