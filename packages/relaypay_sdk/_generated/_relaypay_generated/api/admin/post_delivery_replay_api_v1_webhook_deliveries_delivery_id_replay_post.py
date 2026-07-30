from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.response_post_delivery_replay_api_v1_webhook_deliveries_delivery_id_replay_post import ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    delivery_id: str,
    *,
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
        "url": "/api/v1/webhook_deliveries/{delivery_id}/replay".format(delivery_id=quote(str(delivery_id), safe=""),),
        "cookies": cookies,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost | None:
    if response.status_code == 202:
        response_202 = ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost.from_dict(response.json())



        return response_202

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    delivery_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost]:
    """ Post Delivery Replay

    Args:
        delivery_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost]
     """


    kwargs = _get_kwargs(
        delivery_id=delivery_id,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    delivery_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost | None:
    """ Post Delivery Replay

    Args:
        delivery_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost
     """


    return sync_detailed(
        delivery_id=delivery_id,
client=client,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    delivery_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost]:
    """ Post Delivery Replay

    Args:
        delivery_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost]
     """


    kwargs = _get_kwargs(
        delivery_id=delivery_id,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    delivery_id: str,
    *,
    client: AuthenticatedClient | Client,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost | None:
    """ Post Delivery Replay

    Args:
        delivery_id (str):
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponsePostDeliveryReplayApiV1WebhookDeliveriesDeliveryIdReplayPost
     """


    return (await asyncio_detailed(
        delivery_id=delivery_id,
client=client,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
