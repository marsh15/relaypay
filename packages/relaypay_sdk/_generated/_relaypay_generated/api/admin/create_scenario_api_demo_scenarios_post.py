from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.response_create_scenario_api_demo_scenarios_post import ResponseCreateScenarioApiDemoScenariosPost
from ...models.scenario_create import ScenarioCreate
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    *,
    body: ScenarioCreate,
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
        "url": "/api/demo/scenarios",
        "cookies": cookies,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost | None:
    if response.status_code == 201:
        response_201 = ResponseCreateScenarioApiDemoScenariosPost.from_dict(response.json())



        return response_201

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ScenarioCreate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost]:
    """ Create Scenario

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (ScenarioCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost]
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
    body: ScenarioCreate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost | None:
    """ Create Scenario

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (ScenarioCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost
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
    body: ScenarioCreate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost]:
    """ Create Scenario

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (ScenarioCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost]
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
    body: ScenarioCreate,
    x_csrf_token: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost | None:
    """ Create Scenario

    Args:
        x_csrf_token (None | str | Unset):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):
        body (ScenarioCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseCreateScenarioApiDemoScenariosPost
     """


    return (await asyncio_detailed(
        client=client,
body=body,
x_csrf_token=x_csrf_token,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
