// Run as a Script macro in the PF2e world, with a token selected or an assigned character.
const actor = canvas.tokens.controlled[0]?.actor ?? game.user.character;
if (!actor) return ui.notifications.warn("Выберите токен персонажа");
const skills = Object.fromEntries(Object.entries(actor.skills).map(([slug, skill]) => [slug, {
  rank: skill.rank ?? actor.system.skills[slug]?.rank ?? 0,
  totalModifier: skill.totalModifier ?? skill.mod
}]));
skills.perception = {
  rank: actor.perception.rank ?? actor.system.perception?.rank ?? 0,
  totalModifier: actor.perception.mod ?? actor.perception.totalModifier
};
saveDataToFile(JSON.stringify({name: actor.name, skills}, null, 2), "application/json", "camp-skills.json");
