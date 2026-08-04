from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.response_get_workflow_run_api_admin_v1_environments_environment_id_workflow_runs_run_id_get import ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    environment_id: str,
    run_id: str,
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
        "url": "/api/admin/v1/environments/{environment_id}/workflow-runs/{run_id}".format(environment_id=quote(str(environment_id), safe=""),run_id=quote(str(run_id), safe=""),),
        "cookies": cookies,
    }


    _kwargs["headers"] = headers
    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet | None:
    if response.status_code == 200:
        response_200 = ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    environment_id: str,
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet]:
    """ Get Workflow Run

    Args:
        environment_id (str):
        run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
run_id=run_id,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    environment_id: str,
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet | None:
    """ Get Workflow Run

    Args:
        environment_id (str):
        run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet
     """


    return sync_detailed(
        environment_id=environment_id,
run_id=run_id,
client=client,
authorization=authorization,
relaypay_session=relaypay_session,

    ).parsed

async def asyncio_detailed(
    environment_id: str,
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet]:
    """ Get Workflow Run

    Args:
        environment_id (str):
        run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet]
     """


    kwargs = _get_kwargs(
        environment_id=environment_id,
run_id=run_id,
authorization=authorization,
relaypay_session=relaypay_session,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    environment_id: str,
    run_id: str,
    *,
    client: AuthenticatedClient | Client,
    authorization: None | str | Unset = UNSET,
    relaypay_session: None | str | Unset = UNSET,

) -> HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet | None:
    """ Get Workflow Run

    Args:
        environment_id (str):
        run_id (str):
        authorization (None | str | Unset):
        relaypay_session (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ResponseGetWorkflowRunApiAdminV1EnvironmentsEnvironmentIdWorkflowRunsRunIdGet
     """


    return (await asyncio_detailed(
        environment_id=environment_id,
run_id=run_id,
client=client,
authorization=authorization,
relaypay_session=relaypay_session,

    )).parsed
