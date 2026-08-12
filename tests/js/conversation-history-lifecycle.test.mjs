import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('sirius_pulse/webui/static/pages/conversation-history.js', 'utf8');

for (const selector of ['.conversation-delete', '.chain-toggle', '.chain-detail']) {
  assert.equal(
    source.includes(`scopedPage.$('${selector}')`),
    false,
    `${selector} must use scopedPage.$$ because it is a CSS selector list`,
  );
  assert.equal(
    source.includes(`scopedPage.$$('${selector}')`),
    true,
    `${selector} should be queried as a scoped selector list`,
  );
}

assert.equal(
  source.includes("root.querySelectorAll('.chain-section-header')"),
  true,
  'chain section headers should be bound inside the lazily rendered chain detail',
);

assert.equal(
  source.includes('function renderInjectedToolTags'),
  true,
  'conversation history should render injected tool names as tags',
);

assert.equal(
  source.includes('m.injected_tool_names'),
  true,
  'assistant messages should read injected_tool_names from conversation data',
);

assert.equal(
  source.includes('const visibleMessages = embeddedMode'),
  false,
  'embedded memory view must keep the original message visualization',
);

assert.equal(
  source.includes('if (embeddedMode) return [];'),
  false,
  'embedded memory view must keep rendering stored conversation chains',
);

for (const helper of ['renderInjectedRequestPanel', 'renderInjectedToolDefinitions', 'renderToolCallActivity', 'renderChainDetail']) {
  assert.equal(
    source.includes(`function ${helper}`),
    true,
    `conversation chains should define ${helper} for full injection rendering`,
  );
}

assert.equal(
  source.includes('renderInjectedRequestPanel(parentMessage, chain)'),
  true,
  'full injection should be rendered inside the original chain detail',
);

assert.equal(
  source.includes('点击后加载消息链详情'),
  true,
  'conversation chains should render lazily instead of prebuilding hidden details',
);

assert.equal(
  source.includes('renderChainDetailForButton'),
  true,
  'conversation chain details should be rendered on demand when expanded',
);

for (const helper of [
  'getToolCalls',
  'buildEmbeddedTimelineItems',
  'renderMemoryToolCallMessage',
  'renderMemoryToolResultMessage',
  'renderChainToolCallMessage',
  'renderChainToolResultMessage',
]) {
  assert.equal(
    source.includes(`function ${helper}`),
    true,
    `conversation chains should define ${helper} for tool activity rendering`,
  );
}

assert.equal(
  source.includes("role === 'tool'"),
  true,
  'tool result messages should use a dedicated rendering branch',
);

assert.equal(
  source.includes('工具调用结果'),
  true,
  'tool result cards should be distinguishable in the basic-memory view',
);

const context = {
  createScopedPage: () => ({ $: () => null, $$: () => [], use: () => {}, isActive: () => true }),
  createRealtimeRefresh: () => ({ stop: () => {} }),
  document: {
    createElement: () => ({
      set textContent(value) { this.innerHTML = String(value); },
      innerHTML: '',
    }),
  },
};
context.globalThis = context;
vm.runInNewContext(
  source
    .replace(/^import .*;$/gm, '')
    .replace(/^export /gm, '')
    + '\nglobalThis.asArrayForTest = asArray;'
    + '\nglobalThis.renderChainMessagesForTest = renderChainMessages;'
    + '\nglobalThis.renderInjectedRequestPanelForTest = renderInjectedRequestPanel;'
    + '\nglobalThis.buildEmbeddedTimelineItemsForTest = buildEmbeddedTimelineItems;'
    + '\nglobalThis.renderMemoryToolCallMessageForTest = renderMemoryToolCallMessage;'
    + '\nglobalThis.renderMemoryToolResultMessageForTest = renderMemoryToolResultMessage;',
  context,
);

assert.deepEqual(Array.from(context.asArrayForTest(undefined)), []);
assert.deepEqual(Array.from(context.asArrayForTest({})), []);
assert.deepEqual(Array.from(context.asArrayForTest(['message'])), ['message']);

const renderedInjection = context.renderInjectedRequestPanelForTest(
  {
    injected_request: {
      model: 'gpt-test',
      purpose: 'response_generate',
      system_prompt: 'system prompt',
      messages: [{ role: 'user', content: 'question' }],
      tools: [{
        type: 'function',
        function: {
          name: 'lookup_weather',
          description: 'look up weather',
          parameters: { type: 'object', properties: { city: { type: 'string' } } },
        },
      }],
      tool_choice: 'auto',
      max_tokens: 256,
    },
  },
  [
    {
      role: 'assistant',
      tool_calls: [{
        id: 'call_lookup',
        type: 'function',
        function: { name: 'lookup_weather', arguments: '{"city":"Shanghai"}' },
      }],
    },
    { role: 'tool', tool_call_id: 'call_lookup', content: '{"temperature":30}' },
  ],
);
assert.match(renderedInjection, /最终注入请求/);
assert.match(renderedInjection, /gpt-test/);
assert.match(renderedInjection, /lookup_weather/);
assert.match(renderedInjection, /city/);
assert.match(renderedInjection, /实际调用记录/);
assert.match(renderedInjection, /temperature/);
assert.match(renderedInjection, /查看完整请求 JSON/);

const renderedToolChain = context.renderChainMessagesForTest([
  {
    role: 'assistant',
    tool_calls: [{
      id: 'call_lookup',
      type: 'function',
      function: { name: 'lookup_weather', arguments: '{"city":"Shanghai"}' },
    }],
  },
  { role: 'tool', tool_call_id: 'call_lookup', content: '{"temperature":30}' },
]);
assert.match(renderedToolChain, /工具调用/);
assert.match(renderedToolChain, /lookup_weather/);
assert.match(renderedToolChain, /Shanghai/);
assert.match(renderedToolChain, /工具调用结果 · lookup_weather/);
assert.match(renderedToolChain, /temperature/);

const memoryAssistantMessage = {
  entry_id: 'assistant-memory-1',
  role: 'assistant',
  timestamp: '2026-07-28T12:00:00Z',
  conversation_chain: [
    {
      role: 'assistant',
      content: '临雀姐姐问服务器状态喵～让我看看现在的情况~',
      tool_calls: [{
        id: 'call_00_6Hd3xFX08LA74aUQo5pc7535',
        type: 'function',
        function: {
          name: 'bash',
          arguments: '{"command":"docker ps","timeout_seconds":10}',
        },
      }],
    },
    {
      role: 'tool',
      tool_call_id: 'call_00_6Hd3xFX08LA74aUQo5pc7535',
      content: 'sirius-pulse-v2-test healthy',
    },
  ],
};

const memoryTimeline = context.buildEmbeddedTimelineItemsForTest([memoryAssistantMessage]);
assert.equal(
  JSON.stringify(Array.from(memoryTimeline, item => item.kind)),
  JSON.stringify(['message', 'tool-call', 'tool-result']),
  'embedded memory should place tool events beside the source message',
);
assert.equal(memoryTimeline[1].sourceIndex, 0);
assert.equal(memoryTimeline[2].toolName, 'bash');

const renderedMemoryCall = context.renderMemoryToolCallMessageForTest(memoryTimeline[1]);
assert.match(renderedMemoryCall, /工具调用/);
assert.match(renderedMemoryCall, /bash/);
assert.match(renderedMemoryCall, /call_00_6Hd3xFX08LA74aUQo5pc7535/);
assert.match(renderedMemoryCall, /docker ps/);
assert.match(renderedMemoryCall, /临雀姐姐问服务器状态/);

const renderedMemoryResult = context.renderMemoryToolResultMessageForTest(memoryTimeline[2]);
assert.match(renderedMemoryResult, /工具调用结果 · bash/);
assert.match(renderedMemoryResult, /call_00_6Hd3xFX08LA74aUQo5pc7535/);
assert.match(renderedMemoryResult, /healthy/);

const duplicateMemoryTimeline = context.buildEmbeddedTimelineItemsForTest([
  memoryAssistantMessage,
  { ...memoryAssistantMessage, entry_id: 'assistant-memory-duplicate' },
]);
assert.equal(
  duplicateMemoryTimeline.filter(item => item.kind === 'tool-call').length,
  1,
  'duplicate tool calls should be collapsed by call ID',
);
assert.equal(
  duplicateMemoryTimeline.filter(item => item.kind === 'tool-result').length,
  1,
  'duplicate tool results should be collapsed by tool_call_id',
);
