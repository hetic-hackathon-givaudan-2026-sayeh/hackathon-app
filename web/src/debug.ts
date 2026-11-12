type Question = { id: number; question: string; answer: string };

const healthEl = document.querySelector('#health')!;
const listEl = document.querySelector('#questions')!;
const form = document.querySelector<HTMLFormElement>('#ai-form')!;
const promptEl = document.querySelector<HTMLInputElement>('#prompt')!;
const answerEl = document.querySelector('#ai-answer')!;

async function load() {
  const health = await fetch('/api/health').then((r) => r.json());
  healthEl.textContent = `API OK · ${health.db}`;

  const questions: Question[] = await fetch('/api/questions').then((r) =>
    r.json(),
  );
  listEl.replaceChildren(
    ...questions.map((q) => {
      const li = document.createElement('li');
      li.textContent = `${q.question} : ${q.answer}`;
      return li;
    }),
  );
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  answerEl.textContent = 'Le modèle réfléchit…';
  const res = await fetch('/api/ai', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: promptEl.value }),
  });
  const data = await res.json();
  answerEl.textContent = res.ok ? data.answer : `Erreur : ${data.error}`;
});

load().catch((err) => {
  healthEl.textContent = `API injoignable : ${err}`;
});

type DataSource = { chapter?: string; text: string };

const dataForm = document.querySelector<HTMLFormElement>('#data-form')!;
const dataQuestionEl =
  document.querySelector<HTMLInputElement>('#data-question')!;
const dataAnswerEl = document.querySelector('#data-answer')!;
const dataSourcesEl = document.querySelector('#data-sources')!;

dataForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  dataAnswerEl.textContent = 'Recherche en cours…';
  dataSourcesEl.replaceChildren();
  const res = await fetch('/api/data/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: dataQuestionEl.value }),
  });
  const data = await res.json();
  if (!res.ok) {
    dataAnswerEl.textContent = `Erreur : ${data.error}`;
    return;
  }
  dataAnswerEl.textContent = data.answer;
  const sources: DataSource[] = data.sources ?? [];
  dataSourcesEl.replaceChildren(
    ...sources.map((s) => {
      const li = document.createElement('li');
      li.textContent = `${s.chapter ?? 'Source'} : ${s.text.slice(0, 120)}…`;
      return li;
    }),
  );
});
