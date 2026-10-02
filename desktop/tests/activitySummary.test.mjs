import assert from 'node:assert/strict'
import test from 'node:test'
import { summarizeActivity } from '../src/components/activitySummary.ts'

const at = '2026-10-03T00:00:00Z'
const event = (seq, kind, payload = {}) => ({ seq, kind, payload, created_at: at })

test('groups only explicitly related model and tool activities', () => {
  const events = [
    event(8, 'text_delta', { activity_id: 'model-2', delta: ' 结论 ' }),
    event(1, 'model_call_started', { activity_id: 'model-1', model_name: 'main' }),
    event(2, 'tool_call_started', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', tool_call_name: 'multiply' }),
    event(3, 'tool_call_delta', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', delta: '{"a":2}' }),
    event(4, 'tool_call_finished', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1' }),
    event(5, 'model_call_finished', { activity_id: 'model-1' }),
    event(6, 'tool_result_finished', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', state: 'success' }),
    event(7, 'model_call_started', { activity_id: 'model-2', model_name: 'main' }),
    event(9, 'model_call_finished', { activity_id: 'model-2' }),
  ]
  const summary = summarizeActivity(events)
  assert.equal(summary.models.length, 2)
  assert.deepEqual(summary.models.map((model) => model.id), ['model-1', 'model-2'])
  assert.equal(summary.models[0].tools[0].name, 'multiply')
  assert.equal(summary.models[0].tools[0].input, '{"a":2}')
  assert.equal(summary.models[0].tools[0].resultFinished, true)
  assert.equal(summary.models[1].publicText, ' 结论 ')
  assert.equal(summary.models[1].finished, true)
  assert.equal(summary.toolCount, 1)
  assert.deepEqual(summary.unlinked, [])
})

test('keeps old public text and tool data separate instead of assigning adjacent events', () => {
  const summary = summarizeActivity([
    event(1, 'model_call_started', { model_name: 'old-model' }),
    event(2, 'text_delta', { delta: '旧回答' }),
    event(3, 'tool_call_delta', { tool_call_id: 'old-call', delta: '{"x":1}' }),
    event(4, 'model_call_started', { activity_id: 'model-new', model_name: 'new-model' }),
    event(5, 'text_delta', { delta: '仍是旧事件' }),
    event(6, 'text_delta', { activity_id: 'model-new', delta: '新回答' }),
  ])
  assert.equal(summary.models.length, 1)
  assert.equal(summary.models[0].publicText, '新回答')
  assert.equal(summary.unlinked.length, 4)
  assert.deepEqual(summary.unlinked.map((item) => item.detail), ['old-model', '旧回答', '调用 ID：old-call\n{"x":1}', '仍是旧事件'])
})

test('keeps two tool results under their explicit parent despite interleaved return order', () => {
  const summary = summarizeActivity([
    event(1, 'model_call_started', { activity_id: 'model-1', model_name: 'main' }),
    event(2, 'tool_call_started', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', tool_call_name: 'first' }),
    event(3, 'tool_call_started', { activity_id: 'tool-2', parent_activity_id: 'model-1', tool_call_id: 'call-2', tool_call_name: 'second' }),
    event(4, 'model_call_finished', { activity_id: 'model-1' }),
    event(5, 'tool_result_delta', { activity_id: 'tool-2', parent_activity_id: 'model-1', tool_call_id: 'call-2', delta: 'two' }),
    event(6, 'tool_result_finished', { activity_id: 'tool-2', parent_activity_id: 'model-1', tool_call_id: 'call-2', state: 'success' }),
    event(7, 'tool_result_delta', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', delta: 'one' }),
    event(8, 'tool_result_finished', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', state: 'error' }),
  ])
  assert.equal(summary.toolCount, 2)
  assert.deepEqual(summary.models[0].tools.map((tool) => [tool.id, tool.output, tool.resultState]), [
    ['tool-1', 'one', 'error'], ['tool-2', 'two', 'success'],
  ])
  assert.deepEqual(summary.unlinked, [])
})

test('reports missing parents, inconsistent tool identities and coverage gaps', () => {
  const summary = summarizeActivity([
    event(1, 'model_call_started', { activity_id: 'model-1', model_name: 'main' }),
    event(2, 'tool_call_started', { activity_id: 'tool-orphan', parent_activity_id: 'model-missing', tool_call_id: 'call-x' }),
    event(3, 'tool_call_started', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1' }),
    event(4, 'tool_result_delta', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'wrong', delta: 'wrong result' }),
    event(5, 'tool_result_delta', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', delta: 'right result' }),
    event(6, 'model_call_finished', { activity_id: 'model-missing' }),
    event(7, 'coverage_gap', { source: 'agentscope', native_type: 'FUTURE_EVENT' }),
    event(8, 'tool_result_delta', { activity_id: 'tool-1', parent_activity_id: 'model-wrong', tool_call_id: 'call-1', delta: 'wrong parent' }),
  ])
  assert.equal(summary.models[0].finished, false)
  assert.equal(summary.models[0].tools[0].output, 'right result')
  assert.equal(summary.models[0].tools[0].resultFinished, false)
  assert.equal(summary.coverageGaps, 1)
  assert.deepEqual(summary.unlinked.map((item) => item.seq), [2, 4, 6, 8])
})

test('does not attach a tool to a model that starts later', () => {
  const summary = summarizeActivity([
    event(1, 'tool_call_started', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1' }),
    event(2, 'model_call_started', { activity_id: 'model-1', model_name: 'main' }),
    event(3, 'tool_result_delta', { activity_id: 'tool-1', parent_activity_id: 'model-1', tool_call_id: 'call-1', delta: 'result' }),
  ])
  assert.equal(summary.toolCount, 0)
  assert.deepEqual(summary.unlinked.map((item) => item.seq), [1, 3])
})

test('invalidates repeated activity IDs instead of hanging later events on the first start', () => {
  const summary = summarizeActivity([
    event(1, 'model_call_started', { activity_id: 'model-1', model_name: 'first' }),
    event(2, 'model_call_started', { activity_id: 'model-1', model_name: 'second' }),
    event(3, 'text_delta', { activity_id: 'model-1', delta: 'ambiguous' }),
    event(4, 'model_call_started', { activity_id: 'model-2', model_name: 'valid' }),
    event(5, 'tool_call_started', { activity_id: 'tool-1', parent_activity_id: 'model-2', tool_call_id: 'call-1' }),
    event(6, 'tool_call_started', { activity_id: 'tool-1', parent_activity_id: 'model-2', tool_call_id: 'call-2' }),
    event(7, 'tool_result_delta', { activity_id: 'tool-1', parent_activity_id: 'model-2', tool_call_id: 'call-1', delta: 'ambiguous' }),
  ])
  assert.deepEqual(summary.models.map((model) => model.id), ['model-2'])
  assert.equal(summary.models[0].tools.length, 0)
  assert.deepEqual(summary.unlinked.map((item) => item.seq), [1, 2, 3, 5, 6, 7])
})
