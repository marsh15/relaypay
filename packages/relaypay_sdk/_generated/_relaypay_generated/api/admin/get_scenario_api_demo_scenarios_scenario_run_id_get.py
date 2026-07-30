from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.response_get_scenario_api_demo_scenarios_scenario_run_id_get import ResponseGetScenarioApiDemoScenariosScenarioRunIdGet
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    scenario_run_id: str,
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
        "url": "/api/demo/scenarios/{scenario_run_id}".format(scenario_run_id=quote(str(scenario_run_id), safe=""),),
        "cookies": cookies,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet | None:
    if response.status_code == 200:
        response_200 = ResponseGetScenarioApiDemoScenariosScenarioRunIdGet.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    scenario_run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet]:
    """ Get Scenario

    Args:
        scenario_run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet]
     """


    kwargs = _get_kwargs(
        scenario_run_id=scenario_run_id,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    scenario_run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet | None:
    """ Get Scenario

    Args:
        scenario_run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet
     """


    return sync_detailed(
        scenario_run_id=scenario_run_id,
client=client,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    scenario_run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet]:
    """ Get Scenario

    Args:
        scenario_run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet]
     """


    kwargs = _get_kwargs(
        scenario_run_id=scenario_run_id,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    scenario_run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet | None:
    """ Get Scenario

    Args:
        scenario_run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseGetScenarioApiDemoScenariosScenarioRunIdGet
     """


    return (await asyncio_detailed(
        scenario_run_id=scenario_run_id,
client=client,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
