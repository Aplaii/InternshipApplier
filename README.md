# InternshipApplier

Application locale (interface web façon Gmail) pour trouver des **stages en intelligence
artificielle**, suivre ses candidatures et rédiger les emails avec un modèle local (llama.cpp).

- **Recherche** sur HelloWork, LinkedIn et Welcome to the Jungle, ou ajout d'une offre par son lien.
- **Filtre IA** : seules les offres en IA sont gardées (IA générative / LLM, NLP, vision par ordinateur,
  deep learning, MLOps, machine learning, data science…). Chaque offre reçoit des étiquettes de thèmes.
- **Recherche automatique** : l'application relance seule la dernière recherche à intervalle régulier,
  lit la description des nouvelles offres (adresse du recruteur, thèmes plus précis) et peut préparer
  un brouillon d'email par l'IA. **Aucun email ne part sans vous** : les brouillons sont relus et
  envoyés à la main.
- **Tableau de bord** : offres suivies, candidatures, taux de réponse, activité par semaine, parcours
  des candidatures, thèmes IA, sources et entreprises.
- **Envoi** des candidatures depuis votre boîte (SMTP), avec pièces jointes (CV, lettre).

## Lancer l'application

```bash
./run.sh --open        # crée .venv au premier lancement, puis ouvre http://127.0.0.1:8000
```

Python 3.10 ou plus récent est nécessaire. Les données (base SQLite, documents) sont dans `data/`
(ou dans le dossier indiqué par la variable `IA_DATA_DIR`).

### IA locale (facultatif)

La rédaction des emails utilise un serveur [llama.cpp](https://github.com/ggml-org/llama.cpp) :

```bash
llama-server -hf ggml-org/gemma-3-4b-it-GGUF --no-mmproj -c 8192 --port 8080
```

Adresse, modèle et consignes se règlent dans **Paramètres > IA**.

## Filtre « stages en IA »

Le détecteur (`app/ai_filter.py`) lit le titre de l'offre, puis sa description quand elle est connue.
Sans mot-clé, la recherche lance plusieurs requêtes par site (`intelligence artificielle`,
`machine learning`, `data scientist`) et écarte les offres hors IA. Le filtre se désactive dans
**Paramètres > Automatisation** (ou dans la fenêtre de recherche) ; les offres déjà en base ne sont
alors plus masquées. Les offres ajoutées à la main restent toujours visibles.

## Recherche automatique

Dans **Paramètres > Automatisation** :

| Réglage | Effet |
|---|---|
| Chercher automatiquement | relance la dernière recherche (mots-clés, lieu, sites) toutes les *N* heures |
| Lire la description | récupère le texte des nouvelles offres (et l'email du recruteur s'il y figure) |
| Préparer un brouillon | rédige un email par nouvelle offre avec llama.cpp (au plus *N* par passage) |

La recherche tourne tant que l'application est ouverte ; l'indicateur sous le menu affiche la
prochaine échéance, et une notification signale les nouvelles offres. Le bouton
« Enregistrer et lancer maintenant » déclenche un passage immédiat.

## Développement

```bash
python -m pip install -r requirements-dev.txt
python -m pytest                       # tests hors ligne
RUN_LIVE=1 python -m pytest tests/test_live.py   # vérifie que les trois sites répondent toujours
```

La CI GitHub Actions (`.github/workflows/tests.yml`) lance les tests à chaque push et, chaque lundi,
les tests en ligne des sources pour repérer un site qui aurait changé.

Si Welcome to the Jungle refuse la recherche (HTTP 401/403), sa clé publique Algolia a probablement
changé : renseignez les nouvelles valeurs dans les variables `WTTJ_ALGOLIA_APP_ID` et
`WTTJ_ALGOLIA_API_KEY` (visibles dans les requêtes du site, onglet Réseau du navigateur).
