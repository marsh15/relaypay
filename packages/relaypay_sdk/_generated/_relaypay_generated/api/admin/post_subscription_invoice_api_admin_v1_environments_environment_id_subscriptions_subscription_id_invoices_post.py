from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.response_post_subscription_invoice_api_admin_v1_environments_environment_id_subscriptions_subscription_id_invoices_post import ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost
from ...models.subscription_invoice_create import SubscriptionInvoiceCreate
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    environment_id: str,
    subscription_id: str,
    *,
    body: SubscriptionInvoiceCreate,
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
        "url": "/api/admin/v1/environments/{environment_id}/subscriptions/{subscription_id}/invoices".format(environment_id=quote(str(environment_id), safe=""),subscription_id=quote(str(subscription_id), safe=""),),
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost | None:
    if response.status_code == 201:
        response_201 = ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost.from_dict(response.json())



        return response_201

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    environment_id: str,
    subscription_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: SubscriptionInvoiceCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost]:
    """ Post Subscription Invoice

    Args:
        environment_id (str):
        subscription_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SubscriptionInvoiceCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
subscription_id=subscription_id,
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
    subscription_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: SubscriptionInvoiceCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost | None:
    """ Post Subscription Invoice

    Args:
        environment_id (str):
        subscription_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SubscriptionInvoiceCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost
     """


    return sync_detailed(
        environment_id=environment_id,
subscription_id=subscription_id,
client=client,
body=body,
idempotency_key=idempotency_key,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    subscription_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: SubscriptionInvoiceCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost]:
    """ Post Subscription Invoice

    Args:
        environment_id (str):
        subscription_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SubscriptionInvoiceCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
subscription_id=subscription_id,
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
    subscription_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: SubscriptionInvoiceCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost | None:
    """ Post Subscription Invoice

    Args:
        environment_id (str):
        subscription_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (SubscriptionInvoiceCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostSubscriptionInvoiceApiAdminV1EnvironmentsEnvironmentIdSubscriptionsSubscriptionIdInvoicesPost
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
subscription_id=subscription_id,
client=client,
body=body,
idempotency_key=idempotency_key,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
