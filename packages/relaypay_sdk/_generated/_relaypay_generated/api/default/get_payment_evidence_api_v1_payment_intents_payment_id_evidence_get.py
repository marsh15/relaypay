from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.response_get_payment_evidence_api_v1_payment_intents_payment_id_evidence_get import ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    payment_id: str,
    *,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["Authorization"] = authorization



    cookies = {}
    if relaypay_session is not UNSET:
        cookies["relaypay_session"] = relaypay_session



    

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/payment_intents/{payment_id}/evidence".format(payment_id=quote(str(payment_id), safe=""),),
        "cookies": cookies,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet | None:
    if response.status_code == 200:
        response_200 = ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    payment_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet]:
    """ Get Payment Evidence

    Args:
        payment_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet]
     """


    kwargs = _get_kwargs(
        payment_id=payment_id,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    payment_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet | None:
    """ Get Payment Evidence

    Args:
        payment_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet
     """


    return sync_detailed(
        payment_id=payment_id,
client=client,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    payment_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet]:
    """ Get Payment Evidence

    Args:
        payment_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet]
     """


    kwargs = _get_kwargs(
        payment_id=payment_id,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    payment_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet | None:
    """ Get Payment Evidence

    Args:
        payment_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseGetPaymentEvidenceApiV1PaymentIntentsPaymentIdEvidenceGet
     """


    return (await asyncio_detailed(
        payment_id=payment_id,
client=client,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
