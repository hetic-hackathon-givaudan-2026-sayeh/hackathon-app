type Question = { id: number; question: string; answer: string };

const healthEl = document.querySelector('#health')!;
const listEl = document.querySelector('#questions')!;
const form = document.querySelector<HTMLFormElement>('#ai-form')!;
const promptEl = document.querySelector<HTMLInputElement>('#prompt')!;
const answerEl = document.querySelector('#ai-answer')!;

async function load() {
  const health = await fetch('/api/health').then((r) => r.json());
  healthEl.textContent = `API OK · ${health.db}`;

  const questions: Question[] = await fetch('/api/questions').then((r) => r.json());
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
  answerEl.textContent = '…';
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
