# InternshipApplier — stages FLE

Application locale (interface web façon Gmail) pour trouver des **stages de FLE (français langue
étrangère)**, en priorité dans les **pays nordiques, en Europe du Nord et au Canada**, suivre ses
candidatures et rédiger les emails avec un modèle local (llama.cpp).

- **Annuaire des établissements FLE** : une soixantaine d'Alliances françaises, d'Instituts français,
  d'universités, de lycées français et de ressources (Suède, Norvège, Danemark, Finlande, Islande,
  pays baltes, Pays-Bas, Irlande, Royaume-Uni, Canada). Un clic les importe dans la boîte de réception
  comme **candidatures spontanées**, avec l'adresse de contact quand elle est publique.
- **Recherche** sur LinkedIn (étranger), HelloWork et Welcome to the Jungle (France), sur **plusieurs
  lieux à la fois** (« Sweden; Norway; Canada »), ou ajout d'une offre par son lien (FLE.fr, Fondation
  des Alliances françaises, Indeed…).
- **Stages uniquement** : les offres d'emploi (postes d'enseignant, CDI, remplacements) sont écartées.
- **Candidatures spontanées** suivies à part : dossier dédié, création en un clic (annuaire ou
  formulaire), statistiques séparées dans le tableau de bord.
- **Traduction en français** des titres et descriptions (suédois, néerlandais, anglais…) par le modèle
  local : la liste affiche la traduction, l'original reste consultable.
- **Filtre FLE** : seules les offres d'enseignement du français sont gardées (FLE/FLS, enseignement du
  français, assistanat de langue, Alliance / Institut français, DELF/DALF, didactique, immersion).
  Les motifs couvrent le français, l'anglais et quelques formes nordiques et néerlandaises
  (« fransklärare », « ranskan opettaja »…).
- **Recherche automatique** : l'application relance seule la dernière recherche à intervalle régulier,
  lit la description des nouvelles offres et peut préparer un brouillon d'email par l'IA.
  **Aucun email ne part sans vous** : les brouillons sont relus et envoyés à la main.
- **Tableau de bord** : offres suivies, candidatures, taux de réponse, thèmes FLE, sources, établissements.
- **Envoi** des candidatures depuis votre boîte (SMTP), avec pièces jointes (CV, lettre).

## Lancer l'application

```bash
./run.sh --open        # crée .venv au premier lancement, puis ouvre http://127.0.0.1:8000
```

Python 3.10 ou plus récent est nécessaire. Les données (base SQLite, documents) sont dans `data/`
(ou dans le dossier indiqué par la variable `IA_DATA_DIR`).

### IA locale (facultatif)

La rédaction des emails (consignes adaptées aux candidatures FLE, y compris spontanées) utilise un serveur [llama.cpp](https://github.com/ggml-org/llama.cpp) :

```bash
llama-server -hf ggml-org/gemma-3-4b-it-GGUF --no-mmproj -c 8192 --port 8080
```

Adresse, modèle et consignes se règlent dans **Paramètres > IA**.

## Annuaire des établissements FLE

Les stages FLE à l'étranger sont rarement publiés sur les sites d'emploi : la plupart s'obtiennent
par candidature spontanée. Le bouton **Annuaire des établissements FLE** (barre latérale) liste les
établissements de `app/data/institutions.json`, filtrables par pays et par nom. Les établissements
importés arrivent non lus dans la boîte de réception (source « Annuaire FLE »), avec une description
qui sert à l'IA pour rédiger une candidature spontanée.

Chaque entrée indique si son site a été vérifié (sinon la mention « à vérifier » s'affiche). Les emails
listés sont des adresses génériques publiques ; pour les autres, passez par le formulaire de contact
du site. Plusieurs établissements publient déjà des offres de stage FLE : Alliances françaises
d'Ottawa, d'Edmonton, de Calgary et de Dublin, Institut français de Lituanie, Institut français du
Royaume-Uni. Pour ajouter un établissement, ajoutez une entrée au fichier JSON (champ `id` unique).

## Candidatures spontanées

Le dossier **Candidatures spontanées** regroupe les établissements importés depuis l'annuaire, ceux
ajoutés avec **Nouvelle candidature spontanée** (établissement, email, site, notes) et toute offre
marquée « Candidature spontanée » dans sa fiche. Elles se suivent comme les offres (statut, notes,
brouillon, envoi) ; le tableau de bord compare envois, réponses et entretiens entre candidatures
spontanées et réponses à une offre.

## Traduction en français

Les titres en français sont gardés tels quels ; les autres sont traduits par le serveur llama.cpp
(rien ne part sur Internet). La traduction se fait :

- automatiquement après chaque recherche (**Paramètres > Automatisation**, coché par défaut) ;
- avec le bouton **Traduire N titres** de la barre d'outils (offres déjà en base) ;
- pour la description, avec **Traduire en français** dans la fiche de l'offre (« Voir l'original »
  rebascule sur le texte d'origine).

Sans serveur llama.cpp lancé, la liste affiche les titres d'origine et un message l'indique.

## Filtre « stages FLE »

Le détecteur (`app/fle_filter.py`) lit le titre de l'offre, puis sa description quand elle est connue.
Sans mot-clé, LinkedIn reçoit une seule requête par lieu, avec des « OR »
(`"French teacher" OR "French tutor" OR "enseignant de français"…`). « FLE » seul n'y est pas
utilisé, car LinkedIn y associe « Fleet ». HelloWork et Welcome to the Jungle reçoivent `FLE`,
`français langue étrangère` et `enseignant de français`. Les offres hors FLE sont écartées.
LinkedIn ignore aujourd'hui son filtre « stage » : la recherche ramène surtout des **postes
d'enseignant de français**, qui sont autant d'établissements à contacter pour une candidature spontanée.
« French » seul ne suffit pas (« French-speaking sales intern » est écarté) : il faut un mot
d'enseignement à côté. Le filtre se désactive dans **Paramètres > Automatisation** (ou dans la
fenêtre de recherche). Les offres ajoutées à la main ou depuis l'annuaire restent toujours visibles.

Le lieu par défaut est `Sweden; Norway; Denmark; Finland; Iceland; Estonia; Netherlands; Ireland;
Canada`. Le nombre « Offres max. par site » est réparti entre les lieux, et une pause de 1,5 s
sépare les requêtes LinkedIn : une recherche sur les 9 pays prend environ 30 secondes. Si LinkedIn
répond HTTP 429, raccourcissez la liste ou espacez les recherches automatiques.

### Stage ou offre d'emploi

LinkedIn ignore son propre filtre « stage » : il mélange stages et postes d'enseignant. Une offre
n'est gardée que si son titre ou son contrat dit « stage » (stage, stagiaire, intern, internship,
trainee, co-op, praktik, praktikant, harjoittelija, stagiair…), ou si sa description annonce un
stage (« Type d'emploi : Stage », convention de stage). Un titre d'emploi (FTE, LTO, CDI, CDD,
permanent, tillsvidare, vikariat…) l'emporte sur l'étiquette « Stage » que LinkedIn attribue parfois
à des remplacements. Ces deux détecteurs sont recalculés pour toute la base à chaque démarrage.

Les stages FLE publiés sont rares : à l'étranger, la plupart s'obtiennent par candidature spontanée
(voir l'annuaire).

### Sites interrogés

| Site | Couverture | Remarque |
|---|---|---|
| LinkedIn | monde | une requête « OR » par lieu, exigeant un mot de stage |
| HelloWork | France | un lieu étranger est remplacé par « toute la France » |
| Welcome to the Jungle | France | idem |
| Indeed, JobTeaser | — | non pris en charge : ces sites bloquent les programmes automatiques (HTTP 403). Ajoutez leurs offres avec « Ajouter une offre par lien ». |

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
