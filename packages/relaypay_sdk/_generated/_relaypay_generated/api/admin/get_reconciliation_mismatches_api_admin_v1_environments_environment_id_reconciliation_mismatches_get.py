from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.get_reconciliation_mismatches_api_admin_v1_environments_environment_id_reconciliation_mismatches_get_response_200_item import GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item
from ...models.get_reconciliation_mismatches_api_admin_v1_environments_environment_id_reconciliation_mismatches_get_status_type_0 import GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    environment_id: str,
    *,
    status: GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0 | None | Unset = UNSET,
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

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    elif isinstance(status, GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0):
        json_status = status.value
    else:
        json_status = status
    params["status"] = json_status


    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}


    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/admin/v1/environments/{environment_id}/reconciliation-mismatches".format(environment_id=quote(str(environment_id), safe=""),),
        "params": params,
        "cookies": cookies,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in (_response_200):
            response_200_item = GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item.from_dict(response_200_item_data)



            response_200.append(response_200_item)

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item]]:
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
    status: GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0 | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item]]:
    """ Get Reconciliation Mismatches

    Args:
        environment_id (str):
        status (GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismat
            chesGetStatusType0 | None | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item]]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
status=status,
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
    status: GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0 | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item] | None:
    """ Get Reconciliation Mismatches

    Args:
        environment_id (str):
        status (GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismat
            chesGetStatusType0 | None | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item]
     """


    return sync_detailed(
        environment_id=environment_id,
client=client,
status=status,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    *,
    client: AuthenticatedClient | Client,
    status: GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0 | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item]]:
    """ Get Reconciliation Mismatches

    Args:
        environment_id (str):
        status (GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismat
            chesGetStatusType0 | None | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item]]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
status=status,
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
    status: GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0 | None | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item] | None:
    """ Get Reconciliation Mismatches

    Args:
        environment_id (str):
        status (GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismat
            chesGetStatusType0 | None | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetResponse200Item]
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
client=client,
status=status,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
