from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.response_post_settlement_question_api_admin_v1_environments_environment_id_settlement_questions_post import ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost
from ...models.settlement_question_create import SettlementQuestionCreate
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    environment_id: str,
    *,
    body: SettlementQuestionCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    if not isinstance(x_csrf_token, Unset):
        headers["X-CSRF-Token"] = x_csrf_token

    if not isinstance(authorization, Unset):
        headers["Authorization"] = authorization



    cookies = {}
    if relaypay_session is not UNSET:
        cookies["relaypay_session"] = relaypay_session



    

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/admin/v1/environments/{environment_id}/settlement-questions".format(environment_id=quote(str(environment_id), safe=""),),
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost | None:
    if response.status_code == 200:
        response_200 = ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost]:
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
    body: SettlementQuestionCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost]:
    """ Post Settlement Question

    Args:
        environment_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SettlementQuestionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
body=body,
idempotency_key=idempotency_key,
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
    body: SettlementQuestionCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost | None:
    """ Post Settlement Question

    Args:
        environment_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SettlementQuestionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost
     """


    return sync_detailed(
        environment_id=environment_id,
client=client,
body=body,
idempotency_key=idempotency_key,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: SettlementQuestionCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost]:
    """ Post Settlement Question

    Args:
        environment_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SettlementQuestionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
body=body,
idempotency_key=idempotency_key,
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
    body: SettlementQuestionCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost | None:
    """ Post Settlement Question

    Args:
        environment_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SettlementQuestionCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostSettlementQuestionApiAdminV1EnvironmentsEnvironmentIdSettlementQuestionsPost
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
client=client,
body=body,
idempotency_key=idempotency_key,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
