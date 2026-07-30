from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.mismatch_note import MismatchNote
from ...models.response_post_mismatch_acknowledgement_api_admin_v1_environments_environment_id_reconciliation_mismatches_mismatch_id_acknowledge_post import ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    environment_id: str,
    mismatch_id: str,
    *,
    body: MismatchNote,
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
        "method": "post",
        "url": "/api/admin/v1/environments/{environment_id}/reconciliation-mismatches/{mismatch_id}/acknowledge".format(environment_id=quote(str(environment_id), safe=""),mismatch_id=quote(str(mismatch_id), safe=""),),
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost | None:
    if response.status_code == 200:
        response_200 = ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    environment_id: str,
    mismatch_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MismatchNote,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost]:
    """ Post Mismatch Acknowledgement

    Args:
        environment_id (str):
        mismatch_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MismatchNote):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
mismatch_id=mismatch_id,
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
    environment_id: str,
    mismatch_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MismatchNote,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost | None:
    """ Post Mismatch Acknowledgement

    Args:
        environment_id (str):
        mismatch_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MismatchNote):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost
     """


    return sync_detailed(
        environment_id=environment_id,
mismatch_id=mismatch_id,
client=client,
body=body,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    mismatch_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MismatchNote,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost]:
    """ Post Mismatch Acknowledgement

    Args:
        environment_id (str):
        mismatch_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MismatchNote):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
mismatch_id=mismatch_id,
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
    environment_id: str,
    mismatch_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: MismatchNote,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost | None:
    """ Post Mismatch Acknowledgement

    Args:
        environment_id (str):
        mismatch_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (MismatchNote):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostMismatchAcknowledgementApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesMismatchIdAcknowledgePost
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
mismatch_id=mismatch_id,
client=client,
body=body,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
