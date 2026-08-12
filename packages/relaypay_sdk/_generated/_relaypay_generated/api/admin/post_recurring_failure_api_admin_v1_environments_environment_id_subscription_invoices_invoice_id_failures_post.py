from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.recurring_failure_create import RecurringFailureCreate
from ...models.response_post_recurring_failure_api_admin_v1_environments_environment_id_subscription_invoices_invoice_id_failures_post import ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    environment_id: str,
    invoice_id: str,
    *,
    body: RecurringFailureCreate,
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
        "url": "/api/admin/v1/environments/{environment_id}/subscription-invoices/{invoice_id}/failures".format(environment_id=quote(str(environment_id), safe=""),invoice_id=quote(str(invoice_id), safe=""),),
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost | None:
    if response.status_code == 202:
        response_202 = ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost.from_dict(response.json())



        return response_202

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    environment_id: str,
    invoice_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RecurringFailureCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost]:
    """ Post Recurring Failure

    Args:
        environment_id (str):
        invoice_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (RecurringFailureCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
invoice_id=invoice_id,
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
    invoice_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RecurringFailureCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost | None:
    """ Post Recurring Failure

    Args:
        environment_id (str):
        invoice_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (RecurringFailureCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost
     """


    return sync_detailed(
        environment_id=environment_id,
invoice_id=invoice_id,
client=client,
body=body,
idempotency_key=idempotency_key,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    invoice_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RecurringFailureCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost]:
    """ Post Recurring Failure

    Args:
        environment_id (str):
        invoice_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (RecurringFailureCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
invoice_id=invoice_id,
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
    invoice_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RecurringFailureCreate,
    idempotency_key: str,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost | None:
    """ Post Recurring Failure

    Args:
        environment_id (str):
        invoice_id (str):
        idempotency_key (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (RecurringFailureCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostRecurringFailureApiAdminV1EnvironmentsEnvironmentIdSubscriptionInvoicesInvoiceIdFailuresPost
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
invoice_id=invoice_id,
client=client,
body=body,
idempotency_key=idempotency_key,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
