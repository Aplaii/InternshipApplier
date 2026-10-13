# Stages FLE / DIFLES — Europe et Canada

Deuxième modèle d'InternshipApplier : il recense les **offres de stage en FLE / DIFLES**
(français langue étrangère et seconde) situées **en Europe ou au Canada**. C'est la même
application que celle du dossier parent (boîte de réception, suivi, tableau de bord, emails
rédigés par llama.cpp, recherche automatique), avec un autre profil et sa propre base.

```bash
fle/run.sh --open      # http://127.0.0.1:8001 (le modèle IA reste sur le port 8000)
```

Les deux modèles peuvent tourner en même temps : chacun a ses offres, ses paramètres et ses
documents (`fle/data/` ici, `data/` pour le modèle IA).

## Ce qui est retenu

**Domaine** (`topics.py`) : une offre est gardée si son titre (ou, à défaut, sa description,
de façon répétée) parle de :

| Thème | Exemples |
|---|---|
| FLE | FLE, français langue étrangère, DIFLES, French as a foreign language |
| FLS / immersion | français langue seconde, FSL, French immersion, francisation (Québec) |
| FOS | français sur objectifs spécifiques, français des affaires |
| Intégration / alphabétisation | FLI, alphabétisation, ateliers sociolinguistiques |
| Enseignement du français | professeur / formatrice / assistant(e) de français, French teacher, profesor de francés, Französischlehrer… |
| Didactique des langues | didactique des langues, sciences du langage, plurilinguisme |
| Alliance / Institut français | Alliance française, Institut français |

« Ingénierie pédagogique » est ajouté comme étiquette mais ne suffit pas seul.

**Zone** (`regions.py`) : les offres situées hors d'Europe et du Canada (États-Unis, Maroc,
outre-mer, Asie…) sont écartées, quel que soit le réglage du filtre de domaine. Un lieu
imprécis (« Télétravail », ville française seule) est gardé.

## Où l'application cherche

Sans mot-clé, elle lance les requêtes `FLE`, `français langue étrangère`,
`professeur de français` et `French teacher` sur :

- **LinkedIn** : Union européenne, Suisse, Royaume-Uni et Canada (le nombre d'offres demandé
  est réparti entre ces lieux) ;
- **Welcome to the Jungle** : bureaux dans les pays européens et au Canada ;
- **HelloWork** : France.

Si vous saisissez un lieu (ex. « Montréal »), il remplace la liste ci-dessus. Les offres vues
ailleurs (sites d'Alliances françaises, Le Point du FLE, Guichet-Emplois…) s'ajoutent avec
« Ajouter une offre par lien ».

## Adapter le modèle

Tout le profil est dans `__init__.py` (textes, requêtes, lieux, pays) : il suffit de modifier
ces valeurs. Un autre modèle se crée de la même façon dans un nouveau dossier, lancé avec
`IA_PROFILE=<dossier>`.
