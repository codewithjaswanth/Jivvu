"""
Real Screenshot Production Scenarios & Stress Demo Benchmark:
1. Scenario 1: Asteroid Collision multi with square indicators (border-radius: 4px),
   stem code newline preservation (no comment swallowing), duplicate snippet elimination,
   truth-table evaluation to B, C, D, and live DOM checkbox selection.
2. Scenario 2: Multi-Select Steps & 15-question burst rate-limit stress test (0 500s, 0 429s, latency < 2500ms).
3. Scenario 3: Queue front() mcq with circular radio indicators (border-radius: 50%) -> classified as mcq,
   returns A (10).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["QUIZZY_OFFLINE_TEST_MODE"] = "1"

from backend.app import app
from backend.core.normalizer import normalize_multi_mcq_answer
from backend.schemas import SolveRequest, SolveResponse


def run_node_eval(script_code: str) -> dict:
    """Executes Node.js evaluation script and parses JSON output."""
    res = subprocess.run(
        ["node", "-e", script_code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    if res.returncode != 0:
        raise RuntimeError(f"Node execution failed (code {res.returncode}):\n{res.stderr}\nStdout:\n{res.stdout}")
    try:
        return json.loads(res.stdout.strip())
    except Exception as e:
        raise RuntimeError(f"Failed to parse JSON output: {res.stdout}\nError: {e}")


def test_scenario_1_asteroid_collision_square_indicators_and_dom_selection():
    """
    Scenario 1: Asteroid Collision 'multi' question on CCBP:
    - Visual indicators are square checkboxes (border-radius: 4px).
    - Code snippet starts with single-line comment '// Stack to keep track of asteroids'.
    - Newlines are preserved so the comment does not swallow subsequent code.
    - Code snippet is appended at most once (no duplicate [CODE SNIPPET]: ...).
    - Extractor classifies strictly as q_type: 'multi'.
    - Normalizer and Backend evaluate to B, C, D.
    - Executor selects and locks all 3 target options without toggling off.
    """
    node_test_script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const extractorCode = fs.readFileSync(path.join(process.cwd(), 'extension', 'extractor.js'), 'utf8');
const executorCode = fs.readFileSync(path.join(process.cwd(), 'extension', 'executor.js'), 'utf8');

function matchesSelector(el, sel) {
  if (!sel || !el) return false;
  const parts = sel.split(',').map(s => s.trim());
  for (const p of parts) {
    if (p === '*') return true;
    if (p.startsWith('.')) {
      const cls = p.slice(1);
      if (el.className && el.className.split(/\s+/).includes(cls)) return true;
    } else if (p.startsWith('#')) {
      const id = p.slice(1);
      if (el.getAttribute('id') === id) return true;
    } else if (p.startsWith("[class*='") && p.includes("']")) {
      const target = p.slice(9, p.indexOf("']"));
      if (el.className && el.className.toLowerCase().includes(target.toLowerCase())) return true;
    } else if (p.startsWith("[role='") && p.includes("']")) {
      const target = p.slice(7, p.indexOf("']"));
      if (el.getAttribute('role') === target) return true;
    } else if (p.toLowerCase() === el.tagName.toLowerCase()) {
      return true;
    }
  }
  return false;
}

class MockElement {
  constructor(tag, cls = '', attrs = {}, children = []) {
    this.tagName = tag.toUpperCase();
    this.className = cls;
    this.attributes = { ...attrs };
    this.childNodes = [];
    this.children = [];
    this.parentElement = null;
    this.parentNode = null;
    this.style = {};
    this.clientWidth = 18;
    this.clientHeight = 18;

    children.forEach(c => {
      if (typeof c === 'string') {
        const textNode = {
          nodeType: 3,
          textContent: c,
          innerText: c,
          nodeValue: c,
          parentElement: this,
          parentNode: this
        };
        this.childNodes.push(textNode);
      } else if (c) {
        c.parentElement = this;
        c.parentNode = this;
        this.childNodes.push(c);
        this.children.push(c);
      }
    });
  }

  get nodeType() { return 1; }
  get isConnected() { return true; }

  getAttribute(name) {
    if (name === 'class') return this.className;
    return this.attributes[name] !== undefined ? this.attributes[name] : null;
  }

  setAttribute(name, val) {
    this.attributes[name] = String(val);
    if (name === 'class') this.className = String(val);
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  hasAttribute(name) {
    if (name === 'class') return !!this.className;
    return this.attributes[name] !== undefined;
  }

  get innerText() {
    return this.childNodes.map(c => c.innerText || c.textContent || c.nodeValue || '').join(' ');
  }

  get textContent() {
    return this.childNodes.map(c => c.textContent || c.nodeValue || '').join('');
  }

  getBoundingClientRect() {
    let top = 10;
    if (this.tagName === 'DIV' && this.className.includes('option')) {
      const parent = this.parentElement;
      if (parent) {
        const idx = parent.children.indexOf(this);
        top = 200 + (idx * 50);
      }
    }
    return { left: 10, top: top, width: this.clientWidth || 400, height: this.clientHeight || 40, bottom: top + 40, right: 410 };
  }

  contains(other) {
    let curr = other;
    while (curr) {
      if (curr === this) return true;
      curr = curr.parentElement;
    }
    return false;
  }

  querySelector(sel) {
    const list = this.querySelectorAll(sel);
    return list.length > 0 ? list[0] : null;
  }

  querySelectorAll(sel) {
    const res = [];
    const walk = (node) => {
      for (const ch of node.children) {
        if (matchesSelector(ch, sel)) res.push(ch);
        walk(ch);
      }
    };
    walk(this);
    return res;
  }

  cloneNode(deep = true) {
    const cloned = new MockElement(this.tagName, this.className, { ...this.attributes });
    cloned.style = { ...this.style };
    cloned.clientWidth = this.clientWidth;
    cloned.clientHeight = this.clientHeight;
    if (deep) {
      this.childNodes.forEach(c => {
        if (c.nodeType === 3) {
          cloned.childNodes.push({
            nodeType: 3,
            textContent: c.textContent,
            innerText: c.innerText,
            nodeValue: c.nodeValue,
            parentElement: cloned,
            parentNode: cloned
          });
        } else if (c.nodeType === 1 && typeof c.cloneNode === 'function') {
          const childClone = c.cloneNode(true);
          childClone.parentElement = cloned;
          childClone.parentNode = cloned;
          cloned.childNodes.push(childClone);
          cloned.children.push(childClone);
        }
      });
    }
    return cloned;
  }

  dispatchEvent(ev) { return true; }
  click() {
    this.checked = !this.checked;
    this.className = this.checked ? this.className + ' checked' : this.className.replace(/\bchecked\b/g, '');
  }
}

// Build live CCBP DOM structure for Asteroid Collision question
const codeText = "// Stack to keep track of asteroids\nif (current > 0) {\n    stack.push(current);\n} else {\n    while (!stack.empty() && stack.top() > 0 && stack.top() < abs(current)) {\n        stack.pop();\n    }\n}";

const codeEl = new MockElement('PRE', 'code-block', {}, [codeText]);
const qStemEl = new MockElement('DIV', 'question-stem', {}, ["What are the conditions for an asteroid to be destroyed?", codeEl]);

// 4 Options with square checkbox indicators (border-radius: 4px)
const optDefs = [
  "The current asteroid moves to the right",
  "The asteroid in the stack is moving to the right and smaller than the current asteroid moving to the left",
  "The asteroid in the stack and the current asteroid have equal magnitude moving in opposite directions",
  "The current asteroid is moving to the left and is smaller than the asteroid in the stack moving to the right"
];

const optionRows = optDefs.map((txt, idx) => {
  const boxIndicator = new MockElement('DIV', 'sc-checkbox-box', {}, []);
  boxIndicator.style.width = '18px';
  boxIndicator.style.height = '18px';
  boxIndicator.style.borderRadius = '4px';
  boxIndicator.clientWidth = 18;
  boxIndicator.clientHeight = 18;

  const labelEl = new MockElement('SPAN', 'option-text', {}, [txt]);
  const row = new MockElement('DIV', 'sc-option-card quiz-option', {}, [boxIndicator, labelEl]);
  row.clientWidth = 500;
  row.clientHeight = 40;
  return row;
});

const submitBtn = new MockElement('BUTTON', 'sc-btn-submit', {}, ['SUBMIT']);
const showBtn = new MockElement('BUTTON', 'sc-btn-show', {}, ['SHOW ANSWER']);
const btns = new MockElement('DIV', 'button-container', {}, [showBtn, submitBtn]);

const quizCard = new MockElement('DIV', 'quiz-card sc-card', {}, [qStemEl, ...optionRows, btns]);
const topbar = new MockElement('DIV', 'topbar', {}, ['MCQ PRACTICE | DATA STRUCTURES']);
const body = new MockElement('BODY', '', {}, [topbar, quizCard]);
const doc = new MockElement('HTML', '', {}, [body]);
doc.body = body;
doc.documentElement = doc;

const sandbox = {
  window: {
    location: { href: 'https://learning.ccbp.in/question/asteroid-collision' },
    getComputedStyle: (el) => ({
      width: el.style.width || '18px',
      height: el.style.height || '18px',
      borderRadius: el.style.borderRadius || '0px',
      borderTopLeftRadius: el.style.borderRadius || '0px',
      display: 'block',
      visibility: 'visible',
      opacity: '1'
    }),
    __QUIZZY_LAST_OPTION_ELEMENTS__: []
  },
  document: doc,
  console: console,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  WeakRef: function(el) { this.deref = () => el; },
  WeakMap: WeakMap,
  Set: Set,
  Array: Array,
  Object: Object,
  String: String,
  Number: Number,
  Math: Math,
  RegExp: RegExp
};
sandbox.window.document = doc;
vm.createContext(sandbox);

vm.runInContext(extractorCode, sandbox);
vm.runInContext(executorCode, sandbox);

const extracted = sandbox.window.QuizZyExtractor.extractQuizFromDom();
const indicatorType = sandbox.window.QuizZyExtractor.detectQuestionTypeFromDomIndicators(optionRows, doc);

// Check newline preservation in question
const hasComment = extracted.question.includes('// Stack to keep track of asteroids');
const hasCodeLine = extracted.question.includes('if (current > 0)');
const newlinePreserved = extracted.question.includes('// Stack to keep track of asteroids\n') || extracted.question.includes('// Stack to keep track of asteroids\r\n');
const noDuplicateSnippet = (extracted.question.match(/Reference Code:/g) || []).length <= 1 && !extracted.question.includes('[CODE SNIPPET]:');

// Execute action on options [1, 2, 3] (B, C, D)
(async () => {
  const execRes = await sandbox.window.QuizZyExecutor.executeQuizAction({
    q_type: 'multi',
    action: 'select_only',
    selected_indices: [1, 2, 3],
    selected_texts: [optDefs[1], optDefs[2], optDefs[3]],
    canonical_keys: ['B', 'C', 'D'],
    answer: 'B, C, D',
    options: optDefs
  }, doc);

  const checkedStatuses = optionRows.map(r => r.getAttribute('data-quizzy-multi-checked') === 'true');

  console.log(JSON.stringify({
    success: extracted.success,
    q_type: extracted.q_type,
    indicatorType: indicatorType,
    questionText: extracted.question,
    hasComment: hasComment,
    hasCodeLine: hasCodeLine,
    newlinePreserved: newlinePreserved,
    noDuplicateSnippet: noDuplicateSnippet,
    optionsCount: extracted.options ? extracted.options.length : 0,
    execOk: execRes ? execRes.ok : false,
    checkedStatuses: checkedStatuses
  }));
})();
"""
    res = run_node_eval(node_test_script)
    assert res["success"] is True, f"Extraction failed: {res}"
    assert res["indicatorType"] == "multi", f"Indicator geometry must detect square checkboxes as 'multi': {res}"
    assert res["q_type"] == "multi", f"Extractor must classify Asteroid Collision question as 'multi': {res}"
    assert res["hasComment"] is True, f"Stem must contain single-line comment: {res['questionText']}"
    assert res["hasCodeLine"] is True, f"Stem must contain code line following comment: {res['questionText']}"
    assert res["newlinePreserved"] is True, f"Stem must preserve newline so comment does not swallow code: {res['questionText']}"
    assert res["noDuplicateSnippet"] is True, "Must not append duplicate code blocks or [CODE SNIPPET]:."
    assert res["optionsCount"] == 4, f"Must extract all 4 options, got {res['optionsCount']}"
    assert res["execOk"] is True, f"executeQuizAction must succeed: {res}"
    assert res["checkedStatuses"] == [False, True, True, True], f"Options B, C, D must be checked: {res['checkedStatuses']}"

    # Verify backend multi-select normalization
    opts = [
        "The current asteroid moves to the right",
        "The asteroid in the stack is moving to the right and smaller than the current asteroid moving to the left",
        "The asteroid in the stack and the current asteroid have equal magnitude moving in opposite directions",
        "The current asteroid is moving to the left and is smaller than the asteroid in the stack moving to the right"
    ]
    norm = normalize_multi_mcq_answer(["B", "C", "D"], opts)
    assert norm.canonical_key in ("B, C, D", "B,C,D")
    assert norm.canonical_keys == ["B", "C", "D"]
    assert norm.selected_indices == [1, 2, 3]


def test_scenario_2_15_question_burst_rate_limit_stress_test():
    """
    Scenario 2: Multi-Select Steps & 15-Question Burst Stress Test:
    - Sends 15 varied realistic quiz questions in a rapid burst to /solve.
    - Tests MCQ, Multi, Ordering, and Text questions.
    - Verifies 0 500 Internal Server Errors (100% success).
    - Verifies 0 429 Rate Limit Cooldowns.
    - Verifies per-call latency < 2500ms.
    """
    client = TestClient(app)

    questions = [
        # 1. Physics MCQ
        {
            "subject": "Physics",
            "q_type": "mcq",
            "question": "What is the unit of electrical resistance?",
            "options": ["Volt", "Ampere", "Ohm", "Watt"],
        },
        # 2. Astronomy Text
        {
            "subject": "Astronomy",
            "q_type": "text",
            "question": "What is the closest planet to the Sun?",
            "options": [],
        },
        # 3. Double Hashing Multi
        {
            "subject": "Computer Science",
            "q_type": "multi",
            "question": "What are the advantages of double hashing?",
            "options": ["Reduces clustering", "Uses a second hash function", "Guarantees zero collisions", "O(1) worst case"],
        },
        # 4. Math MCQ
        {
            "subject": "Mathematics",
            "q_type": "mcq",
            "question": "What is the value of 15 * 4?",
            "options": ["45", "60", "75", "90"],
        },
        # 5. Kadane's Max Subarray MCQ
        {
            "subject": "Computer Science",
            "q_type": "mcq",
            "question": "What is the largest sum of a subarray in [2, -1, 2, 3, -9]?",
            "options": ["4", "5", "6", "7"],
        },
        # 6. Asteroid Collision Multi
        {
            "subject": "Data Structures",
            "q_type": "multi",
            "question": "What are the conditions for an asteroid to be destroyed in asteroid collision?",
            "options": [
                "The current asteroid moves to the right",
                "The asteroid in the stack is smaller than the current moving left",
                "Both have equal magnitude moving in opposite directions",
                "The current asteroid is smaller than stack asteroid moving right"
            ],
        },
        # 7. Linear Probing Order
        {
            "subject": "Computer Science",
            "q_type": "order",
            "question": "What is the order of steps in linear probing collision resolution?",
            "options": ["Move to the next slot", "Calculated index is already occupied", "Empty slot is found"],
        },
        # 8. Time Complexity MCQ
        {
            "subject": "Computer Science",
            "q_type": "mcq",
            "question": "Which represents constant time complexity?",
            "options": ["O(n)", "O(1)", "O(log n)", "O(n^2)"],
        },
        # 9. Multiplication Method Text
        {
            "subject": "Computer Science",
            "q_type": "text",
            "question": "What is the result of h(123) using the multiplication method where A = 0.618 and m = 10 ?",
            "options": [],
        },
        # 10. Queue front() MCQ
        {
            "subject": "Data Structures",
            "q_type": "mcq",
            "question": "What are the possible outputs of front() operation for a queue with elements [10, 20, 30]?",
            "options": ["10", "20", "30", "None of these"],
        },
        # 11. Combinatorics MCQ
        {
            "subject": "Mathematics",
            "q_type": "mcq",
            "question": "How many distinct arrangements can be formed from the letters of ARRANGE?",
            "options": ["630", "1260", "2520", "5040"],
        },
        # 12. History Milestones Order
        {
            "subject": "History",
            "q_type": "order",
            "question": "Arrange these historic milestones in chronological order from earliest to latest:",
            "options": ["Moon landing", "World Wide Web", "Wright brothers flight", "Transistor invention"],
        },
        # 13. Chemistry MCQ
        {
            "subject": "Chemistry",
            "q_type": "mcq",
            "question": "What is the atomic number of Carbon?",
            "options": ["5", "6", "7", "8"],
        },
        # 14. Binary Tree Multi
        {
            "subject": "Data Structures",
            "q_type": "multi",
            "question": "What are the advantages of double hashing?",
            "options": ["Reduces clustering", "Uses a second hash function", "Worst case O(1)", "No collisions"],
        },
        # 15. Biology MCQ
        {
            "subject": "Biology",
            "q_type": "mcq",
            "question": "Which organelle is known as the powerhouse of the cell?",
            "options": ["Nucleus", "Mitochondrion", "Ribosome", "Golgi apparatus"],
        },
    ]

    latencies = []
    errors_500 = 0
    errors_429 = 0
    success_count = 0

    for idx, q_data in enumerate(questions, 1):
        t0 = time.perf_counter()
        resp = client.post("/solve", json=q_data)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

        if resp.status_code == 500:
            errors_500 += 1
        elif resp.status_code == 429:
            errors_429 += 1
        elif resp.status_code == 200:
            body = resp.json()
            if body.get("confidence_tier") == "RATE_LIMIT_EXHAUSTED":
                errors_429 += 1
            elif body.get("status") == "ok":
                success_count += 1

    # Verify zero 500 errors
    assert errors_500 == 0, f"Encountered {errors_500} 500 Internal Server Errors in 15-question burst!"

    # Verify zero 429 rate limit errors
    assert errors_429 == 0, f"Encountered {errors_429} 429 Rate Limit Cooldowns in 15-question burst!"

    # Verify 100% success rate
    assert success_count == len(questions), f"Expected {len(questions)} successful answers, got {success_count}"

    # Verify latency contract: average latency < 2500ms
    avg_latency = sum(latencies) / len(latencies)
    assert avg_latency < 2500.0, f"Average latency {avg_latency:.2f}ms exceeded 2500ms threshold!"


def test_scenario_3_queue_front_circular_radio_indicators_classified_as_mcq():
    """
    Scenario 3: Queue front() question with plural stem wording ("What are the possible outputs"):
    - Even though the stem uses "What are the possible outputs", visual indicators are circular radios (border-radius: 50%).
    - Extractor inspects DOM indicator geometry: circle -> classifies strictly as q_type: 'mcq' (NOT 'multi').
    - Backend returns answer 'A' ('10').
    """
    node_test_script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const extractorCode = fs.readFileSync(path.join(process.cwd(), 'extension', 'extractor.js'), 'utf8');

function matchesSelector(el, sel) {
  if (!sel || !el) return false;
  const parts = sel.split(',').map(s => s.trim());
  for (const p of parts) {
    if (p === '*') return true;
    if (p.startsWith('.')) {
      const cls = p.slice(1);
      if (el.className && el.className.split(/\s+/).includes(cls)) return true;
    } else if (p.startsWith('#')) {
      const id = p.slice(1);
      if (el.getAttribute('id') === id) return true;
    } else if (p.startsWith("[class*='") && p.includes("']")) {
      const target = p.slice(9, p.indexOf("']"));
      if (el.className && el.className.toLowerCase().includes(target.toLowerCase())) return true;
    } else if (p.startsWith("[role='") && p.includes("']")) {
      const target = p.slice(7, p.indexOf("']"));
      if (el.getAttribute('role') === target) return true;
    } else if (p.toLowerCase() === el.tagName.toLowerCase()) {
      return true;
    }
  }
  return false;
}

class MockElement {
  constructor(tag, cls = '', attrs = {}, children = []) {
    this.tagName = tag.toUpperCase();
    this.className = cls;
    this.attributes = { ...attrs };
    this.childNodes = [];
    this.children = [];
    this.parentElement = null;
    this.parentNode = null;
    this.style = {};
    this.clientWidth = 18;
    this.clientHeight = 18;

    children.forEach(c => {
      if (typeof c === 'string') {
        const textNode = {
          nodeType: 3,
          textContent: c,
          innerText: c,
          nodeValue: c,
          parentElement: this,
          parentNode: this
        };
        this.childNodes.push(textNode);
      } else if (c) {
        c.parentElement = this;
        c.parentNode = this;
        this.childNodes.push(c);
        this.children.push(c);
      }
    });
  }

  get nodeType() { return 1; }
  get isConnected() { return true; }

  getAttribute(name) {
    if (name === 'class') return this.className;
    return this.attributes[name] !== undefined ? this.attributes[name] : null;
  }

  setAttribute(name, val) {
    this.attributes[name] = String(val);
    if (name === 'class') this.className = String(val);
  }

  hasAttribute(name) {
    if (name === 'class') return !!this.className;
    return this.attributes[name] !== undefined;
  }

  get innerText() {
    return this.childNodes.map(c => c.innerText || c.textContent || c.nodeValue || '').join(' ');
  }

  get textContent() {
    return this.childNodes.map(c => c.textContent || c.nodeValue || '').join('');
  }

  getBoundingClientRect() {
    let top = 10;
    if (this.tagName === 'DIV' && this.className.includes('option')) {
      const parent = this.parentElement;
      if (parent) {
        const idx = parent.children.indexOf(this);
        top = 200 + (idx * 50);
      }
    }
    return { left: 10, top: top, width: this.clientWidth || 400, height: this.clientHeight || 40, bottom: top + 40, right: 410 };
  }

  contains(other) {
    let curr = other;
    while (curr) {
      if (curr === this) return true;
      curr = curr.parentElement;
    }
    return false;
  }

  querySelector(sel) {
    const list = this.querySelectorAll(sel);
    return list.length > 0 ? list[0] : null;
  }

  querySelectorAll(sel) {
    const res = [];
    const walk = (node) => {
      for (const ch of node.children) {
        if (matchesSelector(ch, sel)) res.push(ch);
        walk(ch);
      }
    };
    walk(this);
    return res;
  }

  cloneNode(deep = true) {
    const cloned = new MockElement(this.tagName, this.className, { ...this.attributes });
    cloned.style = { ...this.style };
    cloned.clientWidth = this.clientWidth;
    cloned.clientHeight = this.clientHeight;
    return cloned;
  }
}

// Plural question stem with circular radio button indicators
const stem = "What are the possible outputs of front() operation for a queue with elements [10, 20, 30]?";
const qStemEl = new MockElement('DIV', 'question-stem', {}, [stem]);

const optTexts = ["10", "20", "30", "None of these"];
const optionRows = optTexts.map((txt, idx) => {
  // Circular radio indicator (border-radius: 50%)
  const circleIndicator = new MockElement('DIV', 'sc-radio-circle', {}, []);
  circleIndicator.style.width = '18px';
  circleIndicator.style.height = '18px';
  circleIndicator.style.borderRadius = '50%';
  circleIndicator.clientWidth = 18;
  circleIndicator.clientHeight = 18;

  const labelEl = new MockElement('SPAN', 'option-text', {}, [txt]);
  const row = new MockElement('DIV', 'sc-option-card quiz-option', {}, [circleIndicator, labelEl]);
  row.clientWidth = 400;
  row.clientHeight = 40;
  return row;
});

const submitBtn = new MockElement('BUTTON', 'sc-btn-submit', {}, ['SUBMIT']);
const showBtn = new MockElement('BUTTON', 'sc-btn-show', {}, ['SHOW ANSWER']);
const btns = new MockElement('DIV', 'button-container', {}, [showBtn, submitBtn]);

const quizCard = new MockElement('DIV', 'quiz-card sc-card', {}, [qStemEl, ...optionRows, btns]);
const topbar = new MockElement('DIV', 'topbar', {}, ['MCQ PRACTICE | DATA STRUCTURES']);
const body = new MockElement('BODY', '', {}, [topbar, quizCard]);
const doc = new MockElement('HTML', '', {}, [body]);
doc.body = body;
doc.documentElement = doc;

const sandbox = {
  window: {
    location: { href: 'https://learning.ccbp.in/question/queue-front' },
    getComputedStyle: (el) => ({
      width: el.style.width || '18px',
      height: el.style.height || '18px',
      borderRadius: el.style.borderRadius || '50%',
      borderTopLeftRadius: el.style.borderRadius || '50%',
      display: 'block',
      visibility: 'visible',
      opacity: '1'
    }),
    __QUIZZY_LAST_OPTION_ELEMENTS__: []
  },
  document: doc,
  console: console,
  WeakRef: function(el) { this.deref = () => el; },
  WeakMap: WeakMap,
  Set: Set,
  Array: Array,
  Object: Object,
  String: String,
  Number: Number,
  Math: Math,
  RegExp: RegExp
};
sandbox.window.document = doc;
vm.createContext(sandbox);

vm.runInContext(extractorCode, sandbox);

const extracted = sandbox.window.QuizZyExtractor.extractQuizFromDom();
const indicatorType = sandbox.window.QuizZyExtractor.detectQuestionTypeFromDomIndicators(optionRows, doc);

console.log(JSON.stringify({
  success: extracted.success,
  q_type: extracted.q_type,
  indicatorType: indicatorType,
  options: extracted.options,
  error: extracted.error
}));
"""
    res = run_node_eval(node_test_script)
    assert res["success"] is True, f"Extraction failed: {res}"
    assert res["indicatorType"] == "mcq", f"Circular indicator must classify as 'mcq', got {res['indicatorType']}"
    assert res["q_type"] == "mcq", f"Plural stem with circle radios must classify as 'mcq', got {res['q_type']}"

    # Verify solve response returns A (10)
    client = TestClient(app)
    resp = client.post("/solve", json={
        "subject": "Data Structures",
        "q_type": "mcq",
        "question": "What are the possible outputs of front() operation for a queue with elements [10, 20, 30]?",
        "options": ["10", "20", "30", "None of these"]
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["answer"] == "A"
    assert body["selected_index"] == 0
    assert body["selected_text"] == "10"


def test_scenario_4_max_heap_multi_select_dom_checkbox_selection_and_submission():
    """
    Scenario 4: REQ #192 Max-Heap Array Question on CCBP (learning.ccbp.in):
    - Question: 'Which arrays violate the Max-Heap property?'
    - Options:
        A: <code>[75, 70, 65, 60, 55, 50, 45]</code> (Valid Max-Heap)
        B: <code>[100, 90, 80, 70, 60, 50, 40]</code> (Valid Max-Heap)
        C: <code>[6, 50, 40, 45, 30, 25, 20]</code> (Violates: root 6 < 50)
        D: <code>[90, 70, 80, 85, 60, 50, 40]</code> (Violates: index 1 is 70 < left child index 3 is 85)
    - Row Anatomy: Flex container div where clicking outer row or <code> does NOTHING.
      The click listener is strictly on the left checkbox input / indicator!
    - Verifies:
        1. Extractor extracts q_type: 'multi' and all 4 array options cleanly.
        2. Backend normalizer & solver return canonical_keys: ['C', 'D'], selected_indices: [2, 3], answer: 'C, D'.
        3. Singular fallbacks selected_index: 2 and selected_text are non-None.
        4. Executor physically checks BOTH Checkbox C and Checkbox D via target escalation.
        5. Options A and B remain strictly unchecked.
        6. Pre-submit verification gate succeeds and clicks Submit!
    """
    node_test_script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const extractorCode = fs.readFileSync(path.join(process.cwd(), 'extension', 'extractor.js'), 'utf8');
const executorCode = fs.readFileSync(path.join(process.cwd(), 'extension', 'executor.js'), 'utf8');

function matchesSelector(el, sel) {
  if (!sel || !el) return false;
  const parts = sel.split(',').map(s => s.trim());
  for (const p of parts) {
    if (p === '*') return true;
    if (p.startsWith('.')) {
      const cls = p.slice(1);
      if (el.className && el.className.split(/\s+/).includes(cls)) return true;
    } else if (p.startsWith('#')) {
      const id = p.slice(1);
      if (el.getAttribute('id') === id) return true;
    } else if (p.startsWith("[class*='") && p.includes("']")) {
      const target = p.slice(9, p.indexOf("']"));
      if (el.className && el.className.toLowerCase().includes(target.toLowerCase())) return true;
    } else if (p.startsWith("[role='") && p.includes("']")) {
      const target = p.slice(7, p.indexOf("']"));
      if (el.getAttribute('role') === target) return true;
    } else if (p.startsWith("input[type='") && p.includes("']")) {
      const target = p.slice(12, p.indexOf("']"));
      if (el.tagName.toLowerCase() === 'input' && (el.type || el.getAttribute('type')) === target) return true;
    } else if (p.startsWith("[data-quiz-")) {
      const attr = p.slice(1, -1);
      if (el.hasAttribute && el.hasAttribute(attr)) return true;
    } else if (p.toLowerCase() === el.tagName.toLowerCase()) {
      return true;
    }
  }
  return false;
}

class MockElement {
  constructor(tag, cls = '', attrs = {}, children = []) {
    this.tagName = tag.toUpperCase();
    this.className = cls;
    this.attributes = { ...attrs };
    this.type = attrs.type || (tag.toLowerCase() === 'input' ? 'checkbox' : '');
    this.checked = Boolean(attrs.checked);
    this.childNodes = [];
    this.children = [];
    this.parentElement = null;
    this.parentNode = null;
    this.style = {};
    this.clientWidth = 18;
    this.clientHeight = 18;
    this.listeners = {};

    children.forEach(c => {
      if (typeof c === 'string') {
        const textNode = {
          nodeType: 3,
          textContent: c,
          innerText: c,
          nodeValue: c,
          parentElement: this,
          parentNode: this
        };
        this.childNodes.push(textNode);
      } else if (c) {
        c.parentElement = this;
        c.parentNode = this;
        this.childNodes.push(c);
        this.children.push(c);
      }
    });
  }

  get nodeType() { return 1; }
  get isConnected() { return true; }

  getAttribute(name) {
    if (name === 'class') return this.className;
    if (name === 'type') return this.type;
    return this.attributes[name] !== undefined ? this.attributes[name] : null;
  }

  setAttribute(name, val) {
    this.attributes[name] = String(val);
    if (name === 'class') this.className = String(val);
    if (name === 'type') this.type = String(val);
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  hasAttribute(name) {
    if (name === 'class') return !!this.className;
    return this.attributes[name] !== undefined;
  }

  get innerText() {
    return this.childNodes.map(c => c.innerText || c.textContent || c.nodeValue || '').join(' ');
  }

  get textContent() {
    return this.childNodes.map(c => c.textContent || c.nodeValue || '').join('');
  }

  getBoundingClientRect() {
    let top = 10;
    if (this.tagName === 'DIV' && this.className.includes('option')) {
      const parent = this.parentElement;
      if (parent && parent.children) {
        const idx = parent.children.indexOf(this);
        top = 200 + (idx * 50);
      }
    }
    return { left: 10, top: top, width: this.clientWidth || 400, height: this.clientHeight || 40, bottom: top + 40, right: 410 };
  }

  contains(other) {
    let curr = other;
    while (curr) {
      if (curr === this) return true;
      curr = curr.parentElement;
    }
    return false;
  }

  querySelector(sel) {
    const list = this.querySelectorAll(sel);
    return list.length > 0 ? list[0] : null;
  }

  querySelectorAll(sel) {
    const res = [];
    const walk = (node) => {
      for (const ch of node.children) {
        if (matchesSelector(ch, sel)) res.push(ch);
        walk(ch);
      }
    };
    walk(this);
    return res;
  }

  addEventListener(type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }

  dispatchEvent(ev) {
    const evType = typeof ev === 'string' ? ev : ev.type;
    const evObj = typeof ev === 'string' ? { type: ev, target: this, bubbles: true } : ev;
    if (!evObj.target) evObj.target = this;
    if (this.listeners[evType]) {
      for (const fn of this.listeners[evType]) {
        fn.call(this, evObj);
      }
    }
    if (evObj.bubbles && this.parentElement) {
      this.parentElement.dispatchEvent(evObj);
    }
    return true;
  }

  click() {
    this.dispatchEvent({ type: 'click', bubbles: true, cancelable: true, target: this });
  }

  cloneNode(deep = true) {
    const cloned = new MockElement(this.tagName, this.className, { ...this.attributes });
    cloned.style = { ...this.style };
    cloned.type = this.type;
    cloned.checked = this.checked;
    cloned.clientWidth = this.clientWidth;
    cloned.clientHeight = this.clientHeight;
    if (deep) {
      this.childNodes.forEach(c => {
        if (c.nodeType === 3) {
          cloned.childNodes.push({
            nodeType: 3,
            textContent: c.textContent,
            innerText: c.innerText,
            nodeValue: c.nodeValue,
            parentElement: cloned,
            parentNode: cloned
          });
        } else if (c.nodeType === 1 && typeof c.cloneNode === 'function') {
          const childClone = c.cloneNode(true);
          childClone.parentElement = cloned;
          childClone.parentNode = cloned;
          cloned.childNodes.push(childClone);
          cloned.children.push(childClone);
        }
      });
    }
    return cloned;
  }
}

// Build Max-Heap DOM from REQ #192
const qStemEl = new MockElement('DIV', 'question-stem', {}, ["Which arrays violate the Max-Heap property?"]);

const heapOptions = [
  "[75, 70, 65, 60, 55, 50, 45]",
  "[100, 90, 80, 70, 60, 50, 40]",
  "[6, 50, 40, 45, 30, 25, 20]",
  "[90, 70, 80, 85, 60, 50, 40]"
];

// Option rows where outer row and code elements do NOT toggle checkbox
const rowElements = [];
const inputElements = [];
const indicatorElements = [];

const optionRows = heapOptions.map((optText, idx) => {
  const checkboxInput = new MockElement('INPUT', 'sc-checkbox-input', { type: 'checkbox' });
  checkboxInput.addEventListener('click', () => {
    checkboxInput.checked = !checkboxInput.checked;
    checkboxInput.setAttribute('checked', checkboxInput.checked ? 'true' : 'false');
  });

  const indicatorBox = new MockElement('DIV', 'sc-checkbox-box', {}, []);
  indicatorBox.style.width = '18px';
  indicatorBox.style.height = '18px';
  indicatorBox.style.borderRadius = '4px';
  indicatorBox.addEventListener('click', () => {
    checkboxInput.click();
  });

  const codeEl = new MockElement('CODE', '', {}, [optText]);
  const textWrapper = new MockElement('DIV', 'option-text-wrapper', {}, [codeEl]);

  // Outer row has NO click listener that toggles checkbox!
  const row = new MockElement('DIV', 'sc-option-card quiz-option', {}, [indicatorBox, checkboxInput, textWrapper]);
  row.clientWidth = 500;
  row.clientHeight = 40;

  rowElements.push(row);
  inputElements.push(checkboxInput);
  indicatorElements.push(indicatorBox);
  return row;
});

let submitButtonClicked = false;
const submitBtn = new MockElement('BUTTON', 'sc-btn-submit', {}, ['SUBMIT']);
submitBtn.addEventListener('click', () => {
  submitButtonClicked = true;
});

const btns = new MockElement('DIV', 'button-container', {}, [submitBtn]);
const quizCard = new MockElement('DIV', 'quiz-card sc-card', {}, [qStemEl, ...optionRows, btns]);
const body = new MockElement('BODY', '', {}, [quizCard]);
const doc = new MockElement('HTML', '', {}, [body]);
doc.body = body;
doc.documentElement = doc;

const sandbox = {
  window: {
    location: { href: 'https://learning.ccbp.in/primitive-coding/practice/max-heap-arrays' },
    getComputedStyle: (el) => ({
      width: el.style.width || '18px',
      height: el.style.height || '18px',
      borderRadius: el.style.borderRadius || '0px',
      borderTopLeftRadius: el.style.borderRadius || '0px',
      backgroundColor: el.checked ? 'rgb(37, 99, 235)' : 'rgba(0, 0, 0, 0)',
      display: 'block',
      visibility: 'visible',
      opacity: '1'
    }),
    __QUIZZY_LAST_OPTION_ELEMENTS__: []
  },
  document: doc,
  console: console,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  WeakRef: function(el) { this.deref = () => el; },
  WeakMap: WeakMap,
  Set: Set,
  Array: Array,
  Object: Object,
  String: String,
  Number: Number,
  Math: Math,
  RegExp: RegExp
};
sandbox.window.document = doc;
vm.createContext(sandbox);

vm.runInContext(extractorCode, sandbox);
vm.runInContext(executorCode, sandbox);

const extracted = sandbox.window.QuizZyExtractor.extractQuizFromDom();
const indicatorType = sandbox.window.QuizZyExtractor.detectQuestionTypeFromDomIndicators(optionRows, doc);

// Test Anatomy Resolution on option row 2
const anatomyRow2 = sandbox.window.QuizZyExecutor.resolveOptionAnatomy(optionRows[2].querySelector('code'), optionRows);

(async () => {
  // Execute action selecting C and D ([2, 3])
  const execRes = await sandbox.window.QuizZyExecutor.executeQuizAction({
    q_type: 'multi',
    action: 'select_only',
    selected_indices: [2, 3],
    selected_texts: [heapOptions[2], heapOptions[3]],
    canonical_keys: ['C', 'D'],
    answer: 'C, D',
    options: heapOptions
  });

  const nextRes = await sandbox.window.QuizZyExecutor.clickNextButton();

  console.log(JSON.stringify({
    success: extracted.success,
    q_type: extracted.q_type,
    indicatorType: indicatorType,
    options: extracted.options,
    anatomyFoundInput: Boolean(anatomyRow2 && anatomyRow2.inputEl),
    anatomyFoundIndicator: Boolean(anatomyRow2 && anatomyRow2.indicatorEl),
    execOk: execRes ? execRes.ok : false,
    execVerified: execRes ? execRes.verified : false,
    selectedIndices: execRes ? execRes.selected_indices : [],
    inputCheckedState: inputElements.map(inp => inp.checked),
    submitClicked: submitButtonClicked
  }));
})();
"""
    res = run_node_eval(node_test_script)
    assert res["success"] is True, f"Extraction failed: {res}"
    assert res["q_type"] == "multi", f"Extractor must classify as 'multi', got {res['q_type']}"
    assert res["indicatorType"] == "multi", f"Indicator geometry must detect square checkboxes as 'multi', got {res['indicatorType']}"
    assert len(res["options"]) == 4, f"Must extract 4 options, got {len(res['options'])}"
    assert res["anatomyFoundInput"] is True, "resolveOptionAnatomy must locate sibling checkbox input element"
    assert res["anatomyFoundIndicator"] is True, "resolveOptionAnatomy must locate checkbox indicator element"
    assert res["execOk"] is True, f"executeQuizAction must succeed: {res}"
    assert res["execVerified"] is True, f"executeQuizAction must verify selection: {res}"
    assert res["selectedIndices"] == [2, 3], f"Must select indices 2 and 3: {res['selectedIndices']}"
    assert res["inputCheckedState"] == [False, False, True, True], f"Inputs C and D must be physically checked, A and B unchecked: {res['inputCheckedState']}"
    assert res["submitClicked"] is True, "Submit button must be clicked after multi-select verification"

    # Verify backend solver / normalizer returns C, D with non-None singular fallbacks
    opts = [
        "[75, 70, 65, 60, 55, 50, 45]",
        "[100, 90, 80, 70, 60, 50, 40]",
        "[6, 50, 40, 45, 30, 25, 20]",
        "[90, 70, 80, 85, 60, 50, 40]"
    ]
    norm = normalize_multi_mcq_answer("C, D", opts)
    assert norm.canonical_keys == ["C", "D"]
    assert norm.selected_indices == [2, 3]
    assert norm.selected_index == 2
    assert norm.selected_text == f"{opts[2]}, {opts[3]}"

