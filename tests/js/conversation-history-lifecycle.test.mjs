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

for (const helper of ['getToolCalls', 'renderChainToolCallMessage', 'renderChainToolResultMessage']) {
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
    + '\nglobalThis.renderChainMessagesForTest = renderChainMessages;'
    + '\nglobalThis.renderInjectedRequestPanelForTest = renderInjectedRequestPanel;',
  context,
);

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
