import logging
from collections import defaultdict

from dagster import DagsterEvent, ModeDefinition, PipelineDefinition, execute_pipeline, lambda_solid
from dagster._legacy import pipeline
from dagster.core.events import DagsterEventType
from dagster.core.events.log import EventLogEntry, construct_event_logger
from dagster.loggers import colored_console_logger
from dagster.serdes import deserialize_as


def mode_def(event_callback):
    return ModeDefinition(
        logger_defs={
            "callback": construct_event_logger(event_callback),
            "console": colored_console_logger,
        }
    )


def single_dagster_event(events, event_type):
    assert event_type in events
    return events[event_type][0]


def define_event_logging_pipeline(name, solids, event_callback, deps=None):
    return PipelineDefinition(
        name=name, solid_defs=solids, description=deps, mode_defs=[mode_def(event_callback)]
    )


def test_empty_pipeline():
    events = defaultdict(list)

    def _event_callback(record):
        assert isinstance(record, EventLogEntry)
        if record.is_dagster_event:
            events[record.dagster_event.event_type].append(record)

    pipeline_def = PipelineDefinition(
        name="empty_pipeline", solid_defs=[], mode_defs=[mode_def(_event_callback)]
    )

    result = execute_pipeline(pipeline_def, {"loggers": {"callback": {}, "console": {}}})
    assert result.success
    assert events

    assert (
        single_dagster_event(events, DagsterEventType.PIPELINE_START).pipeline_name
        == "empty_pipeline"
    )
    assert (
        single_dagster_event(events, DagsterEventType.PIPELINE_SUCCESS).pipeline_name
        == "empty_pipeline"
    )


def test_single_solid_pipeline_success():
    events = defaultdict(list)

    @lambda_solid
    def solid_one():
        return 1

    def _event_callback(record):
        if record.is_dagster_event:
            events[record.dagster_event.event_type].append(record)

    pipeline_def = PipelineDefinition(
        name="single_solid_pipeline", solid_defs=[solid_one], mode_defs=[mode_def(_event_callback)]
    )

    result = execute_pipeline(pipeline_def, {"loggers": {"callback": {}}})
    assert result.success
    assert events

    start_event = single_dagster_event(events, DagsterEventType.STEP_START)
    assert start_event.pipeline_name == "single_solid_pipeline"
    assert start_event.dagster_event.solid_name == "solid_one"

    output_event = single_dagster_event(events, DagsterEventType.STEP_OUTPUT)
    assert output_event
    assert output_event.dagster_event.step_output_data.output_name == "result"

    success_event = single_dagster_event(events, DagsterEventType.STEP_SUCCESS)
    assert success_event.pipeline_name == "single_solid_pipeline"
    assert success_event.dagster_event.solid_name == "solid_one"

    assert isinstance(success_event.dagster_event.step_success_data.duration_ms, float)
    assert success_event.dagster_event.step_success_data.duration_ms > 0.0


def test_single_solid_pipeline_failure():
    events = defaultdict(list)

    @lambda_solid
    def solid_one():
        raise Exception("nope")

    def _event_callback(record):
        if record.is_dagster_event:
            events[record.dagster_event.event_type].append(record)

    pipeline_def = PipelineDefinition(
        name="single_solid_pipeline", solid_defs=[solid_one], mode_defs=[mode_def(_event_callback)]
    )

    result = execute_pipeline(pipeline_def, {"loggers": {"callback": {}}}, raise_on_error=False)
    assert not result.success

    start_event = single_dagster_event(events, DagsterEventType.STEP_START)
    assert start_event.pipeline_name == "single_solid_pipeline"

    assert start_event.dagster_event.solid_name == "solid_one"
    assert start_event.level == logging.DEBUG

    failure_event = single_dagster_event(events, DagsterEventType.STEP_FAILURE)
    assert failure_event.pipeline_name == "single_solid_pipeline"

    assert failure_event.dagster_event.solid_name == "solid_one"
    assert failure_event.level == logging.ERROR


def define_simple():
    @lambda_solid
    def yes():
        return "yes"

    @pipeline
    def simple():
        yes()

    return simple


# Generated by printing out an existing serialized event and modifying the event type and
# event_specific_data to types that don't exist yet, to simulate the case where an old
# client deserializes events written from a newer Dagster version
SERIALIZED_EVENT_FROM_THE_FUTURE_WITH_EVENT_SPECIFIC_DATA = '{"__class__": "DagsterEvent", "event_specific_data": {"__class__": "FutureEventData", "foo": null, "bar": null, "baz": null, "metadata_entries": [{"__class__": "EventMetadataEntry", "description": null, "entry_data": {"__class__": "TextMetadataEntryData", "text": "999"}, "label": "pid"}]}, "event_type_value": "EVENT_TYPE_FROM_THE_FUTURE", "logging_tags": {}, "message": "howdy", "pid": null, "pipeline_name": "nonce", "solid_handle": null, "step_handle": null, "step_key": "future_step", "step_kind_value": null}'

SERIALIZED_EVENT_FROM_THE_FUTURE_WITHOUT_EVENT_SPECIFIC_DATA = '{"__class__": "DagsterEvent", "event_specific_data": null, "event_type_value": "EVENT_TYPE_FROM_THE_FUTURE", "logging_tags": {}, "message": "howdy", "pid": null, "pipeline_name": "nonce", "solid_handle": null, "step_handle": null, "step_key": "future_step", "step_kind_value": null}'


def test_event_forward_compat_with_event_specific_data():
    result = deserialize_as(SERIALIZED_EVENT_FROM_THE_FUTURE_WITH_EVENT_SPECIFIC_DATA, DagsterEvent)

    assert (
        result.message
        == 'Could not deserialize event of type EVENT_TYPE_FROM_THE_FUTURE. This event may have been written by a newer version of Dagster. Original message: "howdy"'
    )
    assert result.event_type_value == DagsterEventType.ENGINE_EVENT.value
    assert result.pipeline_name == "nonce"
    assert result.step_key == "future_step"
    assert (
        'Attempted to deserialize class "FutureEventData" which is not in the whitelist.'
        in result.event_specific_data.error.message
    )


def test_event_forward_compat_without_event_specific_data():
    result = deserialize_as(
        SERIALIZED_EVENT_FROM_THE_FUTURE_WITHOUT_EVENT_SPECIFIC_DATA, DagsterEvent
    )

    assert (
        result.message
        == 'Could not deserialize event of type EVENT_TYPE_FROM_THE_FUTURE. This event may have been written by a newer version of Dagster. Original message: "howdy"'
    )
    assert result.event_type_value == DagsterEventType.ENGINE_EVENT.value
    assert result.pipeline_name == "nonce"
    assert result.step_key == "future_step"
    assert (
        "'EVENT_TYPE_FROM_THE_FUTURE' is not a valid DagsterEventType"
        in result.event_specific_data.error.message
    )
