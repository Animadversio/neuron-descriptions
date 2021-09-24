"""Run CNN ablation experiments."""
import argparse
import pathlib
import shutil

import lv.dissection.zoo
import lv.zoo
from lv import datasets, models
from lv.utils import env, training, viz

import numpy as np
import spacy
import torch
import wandb
from torch import cuda
from tqdm import tqdm

EXPERIMENT_RANDOM = 'random'

EXPERIMENT_SEM_AIRLINER = 'airliner'
EXPERIMENT_SEM_CAR = 'car'
EXPERIMENT_SEM_CHIHUAHUA = 'chihuahua'
EXPERIMENT_SEM_FINCH = 'finch'
EXPERIMENT_SEM_FROG = 'frog'
EXPERIMENT_SEM_GAZELLE = 'gazelle'
EXPERIMENT_SEM_HORSE = 'horse'
EXPERIMENT_SEM_SHIP = 'ship'
EXPERIMENT_SEM_TABBY = 'tabby'
EXPERIMENT_SEM_TRUCK = 'truck'

EXPERIMENT_N_OBJECT_WORDS = 'n-object-words'
EXPERIMENT_N_ABSTRACT_WORDS = 'n-abstract-words'
EXPERIMENT_N_CAUSAL_AGENTS = 'n-causal-agents'
EXPERIMENT_N_MATTERS = 'n-matters'
EXPERIMENT_N_PROCESSES = 'n-processes'
EXPERIMENT_N_SUBSTANCES = 'n-substances'
EXPERIMENT_N_THINGS = 'n-things'

EXPERIMENT_N_NOUNS = 'n-nouns'
EXPERIMENT_N_VERBS = 'n-verbs'
EXPERIMENT_N_ADPS = 'n-adpositions'
EXPERIMENT_N_ADJS = 'n-adjectives'

EXPERIMENT_CAPTION_LENGTH = 'caption-length'
EXPERIMENT_MAX_WORD_DIFFERENCE = 'max-word-difference'
EXPERIMENT_PARSE_DEPTH = 'parse-depth'

EXPERIMENTS = (EXPERIMENT_RANDOM, EXPERIMENT_SEM_AIRLINER, EXPERIMENT_SEM_CAR,
               EXPERIMENT_SEM_CHIHUAHUA, EXPERIMENT_SEM_FINCH,
               EXPERIMENT_SEM_FROG, EXPERIMENT_SEM_GAZELLE,
               EXPERIMENT_SEM_HORSE, EXPERIMENT_SEM_SHIP, EXPERIMENT_SEM_TABBY,
               EXPERIMENT_SEM_TRUCK, EXPERIMENT_N_NOUNS, EXPERIMENT_N_VERBS,
               EXPERIMENT_N_ADPS, EXPERIMENT_N_ADJS, EXPERIMENT_CAPTION_LENGTH,
               EXPERIMENT_MAX_WORD_DIFFERENCE, EXPERIMENT_PARSE_DEPTH)

GROUP_RANDOM = 'random'
GROUP_SEMANTIC = 'semantic'
GROUP_SYNTACTIC = 'syntactic'
GROUP_STRUCTURAL = 'structural'

EXPERIMENTS_BY_GROUP = {
    GROUP_RANDOM:
        frozenset({EXPERIMENT_RANDOM}),
    GROUP_SEMANTIC:
        frozenset({
            EXPERIMENT_SEM_AIRLINER,
            EXPERIMENT_SEM_CAR,
            EXPERIMENT_SEM_CHIHUAHUA,
            EXPERIMENT_SEM_FINCH,
            EXPERIMENT_SEM_FROG,
            EXPERIMENT_SEM_GAZELLE,
            EXPERIMENT_SEM_HORSE,
            EXPERIMENT_SEM_SHIP,
            EXPERIMENT_SEM_TABBY,
            EXPERIMENT_SEM_TRUCK,
        }),
    GROUP_SYNTACTIC:
        frozenset({
            EXPERIMENT_N_NOUNS,
            EXPERIMENT_N_VERBS,
            EXPERIMENT_N_ADPS,
            EXPERIMENT_N_ADJS,
        }),
    GROUP_STRUCTURAL:
        frozenset({
            EXPERIMENT_CAPTION_LENGTH,
            EXPERIMENT_MAX_WORD_DIFFERENCE,
            EXPERIMENT_PARSE_DEPTH,
        }),
}

GROUPS_BY_EXPERIMENT = {
    experiment: group for group in EXPERIMENTS_BY_GROUP
    for experiment in EXPERIMENTS_BY_GROUP[group]
}

ORDER_INCREASING = 'increasing'
ORDER_DECREASING = 'decreasing'
ORDERS = (ORDER_DECREASING, ORDER_INCREASING)

CNNS = (lv.dissection.zoo.KEY_RESNET18,)
DATASETS = (
    lv.zoo.KEY_IMAGENET,
    # TODO(evandez): Figure out why this crashes.
    # lv.zoo.KEY_PLACES365,
)

parser = argparse.ArgumentParser(description='run cnn ablation experiments')
parser.add_argument('--cnns',
                    nargs='+',
                    choices=CNNS,
                    default=CNNS,
                    help='cnns to ablate (default: resnet18)')
parser.add_argument('--captioner',
                    nargs=2,
                    default=(lv.zoo.KEY_CAPTIONER_RESNET101, lv.zoo.KEY_ALL),
                    help='captioner model (default: captioner-resnet101 all)')
parser.add_argument(
    '--datasets',
    choices=DATASETS,
    default=DATASETS,
    help='dataset model(s) trained on (default: imagenet, places365)')
parser.add_argument('--experiments',
                    nargs='+',
                    choices=EXPERIMENTS,
                    default=EXPERIMENTS,
                    help='experiments to run (default: all)')
parser.add_argument(
    '--groups',
    nargs='+',
    choices=EXPERIMENTS_BY_GROUP.keys(),
    help='experiment groups to run (default: whatever set by --experiments)')
parser.add_argument('--orders',
                    nargs='+',
                    choices=ORDERS,
                    default=(ORDER_DECREASING,),
                    help='ablation orders to try (default: decreasing)')
parser.add_argument('--data-dir',
                    type=pathlib.Path,
                    help='root dir for datasets (default: project data dir)')
parser.add_argument(
    '--results-dir',
    type=pathlib.Path,
    help='root dir for experiment results (default: project results dir)')
parser.add_argument('--clear-results-dir',
                    action='store_true',
                    help='if set, clear results dir (default: do not)')
parser.add_argument('--ablation-min',
                    type=float,
                    default=0,
                    help='min fraction of neurons to ablate')
parser.add_argument('--ablation-max',
                    type=float,
                    default=.2,
                    help='max fraction of neurons to ablate')
parser.add_argument(
    '--ablation-step-size',
    type=float,
    default=.02,
    help='fraction of neurons to delete at each step (default: .05)')
parser.add_argument(
    '--n-random-trials',
    type=int,
    default=5,
    help='for each experiment, delete an equal number of random '
    'neurons and retest this many times (default: 5)')
parser.add_argument('--device', help='manually set device (default: guessed)')
parser.add_argument('--wandb-project',
                    default='lv',
                    help='wandb project name (default: lv)')
parser.add_argument('--wandb-name',
                    default='cnn-ablations',
                    help='wandb run name (default: cnn-ablations)')
parser.add_argument('--wandb-group',
                    default='applications',
                    help='wandb group name (default: applications)')
parser.add_argument('--wandb-n-samples',
                    type=int,
                    default=25,
                    help='number of samples to upload for each model')
args = parser.parse_args()

wandb.init(project=args.wandb_project,
           name=args.wandb_name,
           group=args.wandb_group,
           config={
               'captioner': '/'.join(args.captioner),
               'ablation_step_size': args.ablation_step_size,
               'n_random_trials': args.n_random_trials,
           })

device = args.device or 'cuda' if cuda.is_available() else 'cpu'

# Prepare necessary directories.
data_dir = args.data_dir or env.data_dir()

results_dir = args.results_dir
if results_dir is None:
    results_dir = env.results_dir() / 'cnn-cert'

if args.clear_results_dir and results_dir.exists():
    shutil.rmtree(results_dir)
results_dir.mkdir(exist_ok=True, parents=True)

# Determine subset of experiments to run.
experiments = set(args.experiments)
if args.groups:
    for group in args.groups:
        experiments |= EXPERIMENTS_BY_GROUP[group]

nlp = spacy.load('en_core_web_lg')
for dataset_name in args.datasets:
    dataset = lv.dissection.zoo.dataset(dataset_name,
                                        factory=training.PreloadedImageFolder)
    assert isinstance(dataset, training.PreloadedImageFolder)
    for cnn_name in args.cnns:
        model_results_dir = results_dir / cnn_name / dataset_name
        model_results_dir.mkdir(exist_ok=True, parents=True)

        cnn, *_ = lv.dissection.zoo.model(cnn_name, dataset_name)
        cnn = models.classifier(cnn).to(device).eval()

        dissected = lv.zoo.datasets(f'{cnn_name}/{dataset_name}',
                                    path=data_dir)
        assert isinstance(dissected, datasets.TopImagesDataset)

        # Obtain captions for every neuron in the CNN.
        captions_file = model_results_dir / 'captions.txt'
        if captions_file.exists():
            print(f'loading captions from {captions_file}')
            with captions_file.open('r') as handle:
                captions = handle.read().split('\n')
        else:
            decoder, *_ = lv.zoo.model(*args.captioner)
            decoder.to(device)
            assert isinstance(decoder, models.Decoder)
            captions = decoder.predict(
                dissected,
                device=device,
                display_progress_as=f'caption {cnn_name}/{dataset_name}',
                strategy='rerank',
                temperature=.5,
                beam_size=50)
            print(f'saving captions to {captions_file}')
            with captions_file.open('w') as handle:
                handle.write('\n'.join(captions))

        # Pretokenize the captions for efficiency.
        tokenized = tuple(nlp.pipe(captions))

        # Begin the experiments! For each one, we will ablate neurons in
        # order of some criterion and measure drops in validation accuracy.
        for experiment in sorted(experiments,
                                 key=lambda exp: GROUPS_BY_EXPERIMENT[exp]):
            group = GROUPS_BY_EXPERIMENT[experiment]
            print(f'\n-------- BEGIN EXPERIMENT: '
                  f'{cnn_name}/{dataset_name}/{group}/{experiment} '
                  '--------')

            # When ablating random neurons, do it a few times to denoise.
            if experiment == EXPERIMENT_RANDOM:
                trials = args.n_random_trials
            else:
                trials = 1

            for trial in range(trials):
                # Group 1: Random ablations. Just choose random neurons.
                if group == GROUP_RANDOM:
                    scores = torch.rand(len(captions)).tolist()

                # Group 2: Semantic ablations. Pick the proper wordnet synset
                # and score according to how many words in the caption belong
                # to a synset descended from that one.
                elif group == GROUP_SEMANTIC:
                    target = nlp(experiment)
                    scores = [toks.similarity(target) for toks in tokenized]

                # Group 3: Syntactic ablations. Count the number of times a POS
                # apears in the caption.
                elif group == GROUP_SYNTACTIC:
                    pos = {
                        EXPERIMENT_N_NOUNS: 'NOUN',
                        EXPERIMENT_N_VERBS: 'VERB',
                        EXPERIMENT_N_ADPS: 'ADP',
                        EXPERIMENT_N_ADJS: 'ADJ',
                    }[experiment]
                    scores = [
                        sum(token.pos_ == pos
                            for token in tokens)
                        for tokens in tokenized
                    ]

                # Group 4: Structural ablations. These are all quite different,
                # so they get their own if branches. Ain't that neat?
                elif experiment == EXPERIMENT_CAPTION_LENGTH:
                    assert group == GROUP_STRUCTURAL
                    scores = [len(tokens) for tokens in tokenized]

                elif experiment == EXPERIMENT_PARSE_DEPTH:
                    assert group == GROUP_STRUCTURAL

                    scores = []
                    for tokens in tqdm(tokenized, desc='compute parse depths'):
                        root = None
                        for token in tokens:
                            if token.dep_ == 'ROOT':
                                root = token
                        assert root is not None

                        deepest = 0
                        frontier = [(root, 0)]
                        while frontier:
                            current, depth = frontier.pop()
                            for child in current.children:
                                frontier.append((child, depth + 1))

                            if depth > deepest:
                                deepest = depth

                        scores.append(deepest)

                else:
                    assert group == GROUP_STRUCTURAL
                    assert experiment == EXPERIMENT_MAX_WORD_DIFFERENCE
                    scores = []
                    for index, tokens in enumerate(tokenized):
                        vectors = torch.stack([
                            torch.from_numpy(token.vector) for token in tokens
                        ])
                        distances = vectors[:, None] - vectors[None, :]
                        distances = (distances**2).sum(dim=-1)
                        score = distances.max().item()
                        scores.append(score)

                # No need to load cached scores, they're easily derived...
                # just always save them for auditing purposes.
                scores_file = model_results_dir / f'{experiment}-scores.pth'
                torch.save(scores, scores_file)

                for order in args.orders:
                    indices = sorted(range(len(captions)),
                                     key=lambda i: scores[i],
                                     reverse=order == ORDER_DECREASING)
                    fractions = np.arange(args.ablation_min, args.ablation_max,
                                          args.ablation_step_size)
                    for fraction in fractions:
                        ablated = indices[:int(fraction * len(indices))]
                        units = dissected.units(ablated)
                        predictions = cnn.predict(
                            dataset,
                            ablate=units,
                            display_progress_as='test ablated '
                            f'{cnn_name}/{dataset_name} '
                            f'(cond={experiment}, '
                            f'trial={trial + 1}, '
                            f'order={order}, '
                            f'frac={fraction:.2f})',
                            device=device)
                        accuracy = cnn.accuracy(dataset,
                                                predictions=predictions,
                                                device=device)

                        # Compute class-by-class breakdown of accuracy.
                        accuracies = {
                            f'accuracy-{dataset.dataset.classes[cat]}': acc for
                            cat, acc in cnn.accuracies(dataset,
                                                       predictions=predictions,
                                                       device=device).items()
                        }

                        # Report to wandb.
                        samples = viz.random_neuron_wandb_images(
                            dissected,
                            captions,
                            indices=ablated,
                            k=args.wandb_n_samples,
                            cnn=cnn_name,
                            data=dataset_name,
                            exp=experiment,
                            order=order,
                            frac=fraction)
                        wandb.log({
                            'cnn': cnn_name,
                            'dataset': dataset_name,
                            'group': group,
                            'experiment': experiment,
                            'trial': trial + 1,
                            'order': order,
                            'frac_ablated': fraction,
                            'n_ablated': len(ablated),
                            'accuracy': accuracy,
                            'samples': samples,
                            **accuracies,
                        })
