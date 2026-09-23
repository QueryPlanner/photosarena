const gameView = document.querySelector("#game-view");
const loadingView = document.querySelector("#loading-view");
const errorView = document.querySelector("#error-view");
const resultsView = document.querySelector("#results-view");
const choicesNode = document.querySelector("#choices");
const roundLabel = document.querySelector("#round-label");
const progress = document.querySelector(".progress-track");
const progressFill = document.querySelector(".progress-fill");
const gameStatus = document.querySelector("#game-status");
const retryButton = document.querySelector("#retry-button");
const playAgainButton = document.querySelector("#play-again");

let requestInFlight = false;
let retryAction = null;
let pendingVote = null;

function show(view) {
  for (const candidate of [gameView, loadingView, errorView, resultsView]) {
    candidate.hidden = candidate !== view;
  }
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || "The request could not be completed.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function makeChoice(choice, index, ballotId) {
  const button = document.createElement("button");
  button.className = "photo-choice";
  button.type = "button";
  button.dataset.choiceId = choice.id;
  button.setAttribute("aria-label", `Choose photo ${index + 1}`);
  button.setAttribute("aria-pressed", "false");

  const lip = document.createElement("span");
  lip.className = "photo-choice__lip";
  lip.setAttribute("aria-hidden", "true");

  const face = document.createElement("span");
  face.className = "photo-choice__face";

  const image = document.createElement("img");
  image.className = "photo-choice__image";
  image.src = choice.image_url;
  image.alt = "";
  image.loading = "eager";
  image.decoding = "async";

  const innerFrame = document.createElement("span");
  innerFrame.className = "photo-choice__frame";
  innerFrame.setAttribute("aria-hidden", "true");

  face.append(image, innerFrame);
  button.append(lip, face);
  button.addEventListener("click", () => choose(button, choice.id, ballotId));
  return button;
}

function renderGame(game) {
  requestInFlight = false;
  show(gameView);
  roundLabel.textContent = `ROUND ${game.round} OF ${game.total_rounds}`;
  progress.setAttribute("aria-valuenow", String(game.round - 1));
  progressFill.style.width = `${((game.round - 1) / game.total_rounds) * 100}%`;
  gameStatus.textContent = `Round ${game.round} of ${game.total_rounds}. Choose one photo.`;
  choicesNode.replaceChildren(
    ...game.choices.map((choice, index) => makeChoice(choice, index, game.ballot_id)),
  );
}

function renderResults(results) {
  requestInFlight = false;
  show(resultsView);
  const list = document.querySelector("#ranking-list");
  list.replaceChildren(...results.ranking.slice(0, 10).map((item, index) => {
    const row = document.createElement("li");
    row.className = "ranking-row";

    const rank = document.createElement("span");
    rank.className = "ranking-rank";
    rank.textContent = `#${index + 1}`;

    const image = document.createElement("img");
    image.className = "ranking-thumb";
    image.src = item.image_url;
    image.alt = `Crowd rank ${index + 1}`;
    image.loading = "lazy";

    const label = document.createElement("span");
    label.textContent = `Photo ${index + 1}`;

    const score = document.createElement("span");
    score.className = "ranking-score";
    score.textContent = `${item.votes} ${item.votes === 1 ? "vote" : "votes"}`;

    row.append(rank, image, label, score);
    return row;
  }));
}

function showError(error) {
  requestInFlight = false;
  document.querySelector("#error-message").textContent = error.message;
  show(errorView);
}

async function startGame() {
  retryAction = startGame;
  retryButton.textContent = "Try again";
  pendingVote = null;
  requestInFlight = true;
  show(loadingView);
  try {
    renderGame(await requestJson("/api/games", { method: "POST", body: "{}" }));
  } catch (error) {
    showError(error);
  }
}

async function choose(button, choiceId, ballotId) {
  if (requestInFlight) return;
  button.classList.add("is-selected");
  button.setAttribute("aria-pressed", "true");
  button.setAttribute("aria-label", `${button.getAttribute("aria-label")} selected`);
  for (const choice of choicesNode.querySelectorAll("button")) choice.disabled = true;
  const selectedIndex = [...choicesNode.querySelectorAll("button")].indexOf(button) + 1;
  gameStatus.textContent = `Photo ${selectedIndex} selected. Recording your vote.`;
  pendingVote = { ballot_id: ballotId, choice_id: choiceId };
  retryAction = submitPendingVote;
  retryButton.textContent = "Retry this vote";
  await submitPendingVote();
}

async function submitPendingVote() {
  if (requestInFlight || !pendingVote) return;
  requestInFlight = true;
  show(loadingView);

  try {
    const response = await requestJson("/api/votes", {
      method: "POST",
      body: JSON.stringify(pendingVote),
    });
    await new Promise((resolve) => window.setTimeout(resolve, 95));
    pendingVote = null;
    if (response.completed) renderResults(response);
    else renderGame(response);
  } catch (error) {
    retryAction = error.status === 410 ? startGame : submitPendingVote;
    retryButton.textContent = error.status === 410 ? "Start a new game" : "Retry this vote";
    showError(error);
  }
}

retryButton.addEventListener("click", () => {
  if (retryAction) retryAction();
});
playAgainButton.addEventListener("click", startGame);
startGame();
