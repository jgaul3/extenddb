# Copyright 2026 ExtendDB contributors
# SPDX-License-Identifier: Apache-2.0

"""The answer to a request body with two faults, in both member orders.

Amazon DynamoDB answers a body with two type faults (members it cannot
convert to their type) with the fault of the member written first, and a
type fault beside a validation fault with the type fault in either order.
Two validation faults get one answer in both orders.
Measured in us-east-1 on 2026-11-12 (Query, Scan, TransactWriteItems) and
2026-11-16 (GetItem, PutItem, UpdateItem). Each row pins the service's
status, error type and message for one body in one member order.

ExtendDB words a type fault with serde_json's text (#1012), so on ExtendDB a
type-fault row checks that text instead. It names the member written first,
so a server that reads the members in another order fails the row.

The bodies are posted with their members in the order written, signed as the
client's caller, because botocore refuses these values before it sends them.

Dual-target against Amazon DynamoDB and ExtendDB.
"""

from __future__ import annotations

from typing import Any

import boto3
import pytest

from conftest import _is_real_dynamodb, scoped_table
from raw_signed_request import answer, signed_post

ON_EXTENDDB = not _is_real_dynamodb()


def _differs(reason: str):
    """ExtendDB answers the row otherwise, in every build."""
    return pytest.mark.xfail(ON_EXTENDDB, reason=reason, raises=AssertionError, strict=True)


# 720: a fractional Limit is a SerializationException on ExtendDB.
FRACTIONAL_LIMIT = _differs("#720: a fractional Limit answers SerializationException")
# 778: a Limit fault after an ExpressionAttributeValues fault answers the value fault.
LIMIT_AFTER_VALUE = _differs("#778: the value fault written first is answered")
# 629: an unknown type descriptor is a SerializationException on ExtendDB.
UNKNOWN_DESCRIPTOR = _differs("#629: an unknown type descriptor answers SerializationException")

SERDE = "Start of structure or map found where not expected: invalid type: "

P = {"S": "p0"}
KEY = {"pk": {"S": "k0"}}
EAV_P = {"ExpressionAttributeValues": {":p": P}}
EAV_EMPTY = {"ExpressionAttributeValues": {":p": P, ":a": {}}}

STRING_TO_INTEGER = (400, "SerializationException", "STRING_VALUE cannot be converted to Integer")
NUMBER_TO_STRING = (400, "SerializationException", "NUMBER_VALUE cannot be converted to String")
UNEXPECTED_TOKEN = (400, "SerializationException", "Unexpected token received from parser")
UNEXPECTED_FIELD_TYPE = (400, "SerializationException", "Unexpected field type")
LIMIT_BELOW_ONE = (
    400,
    "ValidationException",
    "1 validation error detected: Value '0' at 'limit' failed to satisfy constraint: "
    "Member must have value greater than or equal to 1",
)
EMPTY_B = (
    400,
    "ValidationException",
    "ExpressionAttributeValues contains invalid value: Supplied AttributeValue is empty, "
    "must contain exactly one of the supported datatypes for key :b",
)

# serde_json's description of each faulty member, as ExtendDB reports it.
LIMIT = 'string "0", expected i64'
CONSISTENT_READ = 'string "x", expected a boolean'
NUMBER_FOR_STRING = "integer `{}`, expected a string"
STRING_FOR_MAP = 'string "x", expected a map'


def _query(table: str, *members: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"TableName": table, "KeyConditionExpression": "pk = :p"}
    for member in members:
        body.update(member)
    return body


def _scan(table: str, values: dict[str, Any]) -> dict[str, Any]:
    return {
        "TableName": table,
        "FilterExpression": "pk IN (:p, :a, :b)",
        "ExpressionAttributeValues": {":p": P, **values},
    }


def _put(put: dict[str, Any]) -> dict[str, Any]:
    return {"TransactItems": [{"Put": put}]}


# (operation, body, service answer, ExtendDB's serde_json text or None)
ROWS = [
    # Two type faults: the member written first.
    pytest.param(
        "Query",
        lambda t: _query(t, EAV_P, {"Limit": "0"}, {"ConsistentRead": "x"}),
        STRING_TO_INTEGER,
        LIMIT,
        id="query_limit_then_consistent_read",
    ),
    pytest.param(
        "Query",
        lambda t: _query(t, EAV_P, {"ConsistentRead": "x"}, {"Limit": "0"}),
        UNEXPECTED_TOKEN,
        CONSISTENT_READ,
        id="query_consistent_read_then_limit",
    ),
    pytest.param(
        "Query",
        lambda t: _query(t, EAV_P, {"Select": 7}, {"Limit": "0"}),
        NUMBER_TO_STRING,
        NUMBER_FOR_STRING.format(7),
        id="query_select_then_limit",
    ),
    pytest.param(
        "Query",
        lambda t: _query(t, EAV_P, {"Limit": "0"}, {"Select": 7}),
        STRING_TO_INTEGER,
        LIMIT,
        id="query_limit_then_select",
    ),
    pytest.param(
        "TransactWriteItems",
        lambda t: _put({"TableName": 5, "Item": "x"}),
        NUMBER_TO_STRING,
        NUMBER_FOR_STRING.format(5),
        id="transact_put_table_name_then_item",
    ),
    pytest.param(
        "TransactWriteItems",
        lambda t: _put({"Item": "x", "TableName": 5}),
        UNEXPECTED_FIELD_TYPE,
        STRING_FOR_MAP,
        id="transact_put_item_then_table_name",
    ),
    pytest.param(
        "GetItem",
        lambda t: {"TableName": t, "Key": KEY, "ConsistentRead": "x", "ProjectionExpression": 5},
        UNEXPECTED_TOKEN,
        CONSISTENT_READ,
        id="get_consistent_read_then_projection",
    ),
    pytest.param(
        "GetItem",
        lambda t: {"TableName": t, "Key": KEY, "ProjectionExpression": 5, "ConsistentRead": "x"},
        NUMBER_TO_STRING,
        NUMBER_FOR_STRING.format(5),
        id="get_projection_then_consistent_read",
    ),
    pytest.param(
        "PutItem",
        lambda t: {"TableName": t, "Item": KEY, "ReturnValues": 5, "Expected": "x"},
        NUMBER_TO_STRING,
        NUMBER_FOR_STRING.format(5),
        id="put_return_values_then_expected",
    ),
    pytest.param(
        "PutItem",
        lambda t: {"TableName": t, "Item": KEY, "Expected": "x", "ReturnValues": 5},
        UNEXPECTED_FIELD_TYPE,
        STRING_FOR_MAP,
        id="put_expected_then_return_values",
    ),
    pytest.param(
        "UpdateItem",
        lambda t: {"TableName": t, "Key": KEY, "ReturnValues": 5, "ExpressionAttributeNames": "x"},
        NUMBER_TO_STRING,
        NUMBER_FOR_STRING.format(5),
        id="update_return_values_then_names",
    ),
    pytest.param(
        "UpdateItem",
        lambda t: {"TableName": t, "Key": KEY, "ExpressionAttributeNames": "x", "ReturnValues": 5},
        UNEXPECTED_FIELD_TYPE,
        STRING_FOR_MAP,
        id="update_names_then_return_values",
    ),
    # A type fault beside a validation fault: the type fault in both orders.
    pytest.param(
        "Query",
        lambda t: _query(t, {"Limit": "0"}, EAV_EMPTY),
        STRING_TO_INTEGER,
        LIMIT,
        id="query_string_limit_then_empty_value",
    ),
    pytest.param(
        "Query",
        lambda t: _query(t, EAV_EMPTY, {"Limit": "0"}),
        STRING_TO_INTEGER,
        None,
        id="query_empty_value_then_string_limit",
        marks=LIMIT_AFTER_VALUE,
    ),
    # Two validation faults: one answer in both orders. The service reads a
    # Limit of 0.0 as 0, and answers {"X": "1"} with the text for an empty
    # value. ExtendDB answers each of the two as a type fault (#720, #629).
    pytest.param(
        "Query",
        lambda t: _query(t, {"Limit": 0.0}, EAV_EMPTY),
        LIMIT_BELOW_ONE,
        None,
        id="query_float_limit_then_empty_value",
        marks=FRACTIONAL_LIMIT,
    ),
    pytest.param(
        "Query",
        lambda t: _query(t, EAV_EMPTY, {"Limit": 0.0}),
        LIMIT_BELOW_ONE,
        None,
        id="query_empty_value_then_float_limit",
        marks=LIMIT_AFTER_VALUE,
    ),
    pytest.param(
        "Scan",
        lambda t: _scan(t, {":b": {}, ":a": {"X": "1"}}),
        EMPTY_B,
        None,
        id="scan_empty_b_then_unknown_a",
    ),
    pytest.param(
        "Scan",
        lambda t: _scan(t, {":a": {"X": "1"}, ":b": {}}),
        EMPTY_B,
        None,
        id="scan_unknown_a_then_empty_b",
        marks=UNKNOWN_DESCRIPTOR,
    ),
]


@pytest.fixture(scope="module")
def hash_table(dynamodb_client):
    """Hash-only table for the module, deleted on teardown."""
    with scoped_table(dynamodb_client) as name:
        yield name


def _answer(client, endpoint_url: str | None, operation: str, body: dict[str, Any]):
    """The status, the error type and the message of ``body``'s answer."""
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    return answer(
        signed_post(
            endpoint_url or client.meta.endpoint_url,
            creds.access_key,
            creds.secret_key,
            client.meta.region_name,
            operation,
            body,
            session_token=creds.token,
        )
    )


@pytest.mark.parametrize("operation, body, expected, extenddb_text", ROWS)
def test_two_fault_body_gets_the_service_answer(
    dynamodb_client, endpoint_url, hash_table, operation, body, expected, extenddb_text
):
    if endpoint_url and extenddb_text:
        expected = (400, "SerializationException", SERDE + extenddb_text)
    assert _answer(dynamodb_client, endpoint_url, operation, body(hash_table)) == expected
