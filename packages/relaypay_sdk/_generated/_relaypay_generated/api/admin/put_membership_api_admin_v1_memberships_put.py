from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.membership_update import MembershipUpdate
from ...models.response_put_membership_api_admin_v1_memberships_put import ResponsePutMembershipApiAdminV1MembershipsPut
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    *,
    body: MembershipUpdate,
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



    

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/admin/v1/memberships",
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut | None:
    if response.status_code == 200:
        response_200 = ResponsePutMembershipApiAdminV1MembershipsPut.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: MembershipUpdate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut]:
    """ Put Membership

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MembershipUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut]
     """


    kwargs = _get_kwargs(
        body=body,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    *,
    client: AuthenticatedClient | Client,
    body: MembershipUpdate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut | None:
    """ Put Membership

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MembershipUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut
     """


    return sync_detailed(
        client=client,
body=body,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: MembershipUpdate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut]:
    """ Put Membership

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MembershipUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut]
     """


    kwargs = _get_kwargs(
        body=body,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: MembershipUpdate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut | None:
    """ Put Membership

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MembershipUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePutMembershipApiAdminV1MembershipsPut
     """


    return (await asyncio_detailed(
        client=client,
body=body,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
