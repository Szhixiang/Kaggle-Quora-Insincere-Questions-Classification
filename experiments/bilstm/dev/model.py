import torch
import numpy as np
from torch import nn

from gensim.models import Word2Vec

from qiqc.builder import build_attention
from qiqc.builder import build_aggregator
from qiqc.builder import build_encoder
from qiqc.features import WordFeature
from qiqc.models import BinaryClassifier


def build_sampler(batchsize, i_cv, epoch, weights):
    return None


def build_models(config, vocab, pretrained_vectors, df):
    models = []
    pos_weight = torch.FloatTensor([config['pos_weight']]).to(config['device'])
    external_vectors = np.stack(
        [wv.vectors for wv in pretrained_vectors.values()])
    external_vectors = external_vectors.mean(axis=0)
    word_features = WordFeature(
        vocab, external_vectors, config['vocab']['min_count'])

    if config['model']['embed']['finetune']:
        word_features.finetune(Word2Vec, df)

    if config['model']['embed']['extra_features'] is not None:
        word_features.prepare_extra_features(
            df, vocab.token2id, config['model']['embed']['extra_features'])

    for i in range(config['cv']):
        add_noise = config['model']['embed']['add_noise']
        embedding_vectors = word_features.build_feature(add_noise=add_noise)
        embedding = nn.Embedding.from_pretrained(
            torch.Tensor(embedding_vectors), freeze=True)
        lossfunc = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        model = build_model(config, embedding, lossfunc)
        models.append(model)

    return models, word_features.unk


def build_model(config, embedding, lossfunc):
    encoder = Encoder(config['model'], embedding)
    clf = BinaryClassifier(config['model'], encoder, lossfunc)
    return clf


def build_sentence_feature():
    return StatisticSentenceFeature()


class StatisticSentenceFeature(object):

    out_size = 6

    def extract_features(self, sentence):
        feature = {}
        tokens = sentence.split()
        feature['n_chars'] = len(sentence)
        feature['n_caps'] = sum(1 for char in sentence if char.isupper())
        feature['caps_rate'] = feature['n_caps'] / feature['n_chars']
        feature['n_words'] = len(tokens)
        feature['unique_words'] = len(set(tokens))
        feature['unique_rate'] = feature['unique_words'] / feature['n_words']
        features = np.array(list(feature.values()))
        return features

    def fit_transform(self, features):
        self.mean = features.mean()
        self.std = features.std()
        return (features - self.mean) / self.std

    def transform(self, features):
        assert hasattr(self, 'mean'), hasattr(self, 'std')
        return (features - self.mean) / self.std


class Encoder(nn.Module):

    def __init__(self, config, embedding):
        super().__init__()
        self.config = config
        self.embedding = embedding
        config['encoder']['n_input'] = self.embedding.embedding_dim
        if self.config['embed']['dropout1d'] > 0:
            self.dropout1d = nn.Dropout(config['embed']['dropout1d'])
        if self.config['embed']['dropout2d'] > 0:
            self.dropout2d = nn.Dropout2d(config['embed']['dropout2d'])
        self.encoder = build_encoder(
            config['encoder']['name'])(config['encoder'])
        self.aggregator = build_aggregator(
            config['encoder']['aggregator'])
        if self.config['encoder'].get('attention') is not None:
            self.attn = build_attention(config['encoder']['attention'])(
                config['encoder']['n_hidden'] * config['encoder']['out_scale'])

    def forward(self, X, X2, mask):
        h = self.embedding(X)
        if self.config['embed']['dropout1d'] > 0:
            h = self.dropout1d(h)
        if self.config['embed']['dropout2d'] > 0:
            h = self.dropout2d(h)
        h = self.encoder(h, mask)
        if self.config['encoder'].get('attention') is not None:
            h = self.attn(h, mask)
        h = self.aggregator(h, mask)
        if self.config['encoder']['sentence_features'] > 0:
            h = torch.cat([h, X2], dim=1)
        return h