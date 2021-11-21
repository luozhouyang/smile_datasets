import abc
import logging
from enum import auto
from typing import List

import tensorflow as tf
from smile_datasets import utils
from smile_datasets.dataset import Datapipe, Dataset
from smile_datasets.simcse.parsers import ParserForUnsupervisedSimCSE
from tokenizers import BertWordPieceTokenizer

from . import readers
from .example import ExampleForUnsupervisedSimCSE


class DatasetForUnsupervisedSimCSE(Dataset):
    """dataset for unsupervised SimCSE"""

    def __len__(self):
        raise NotImplementedError()

    def __getitem__(self, index) -> ExampleForUnsupervisedSimCSE:
        raise NotImplementedError()

    def save_tfrecord(self, output_files, **kwargs):
        """Convert examples to tfrecord"""

        def _encoding(example: ExampleForUnsupervisedSimCSE):
            feature = {
                "input_ids": utils.int64_feature([int(x) for x in example.input_ids]),
                "segment_ids": utils.int64_feature([int(x) for x in example.segment_ids]),
                "attention_mask": utils.int64_feature([int(x) for x in example.attention_mask]),
            }
            return feature

        utils.save_tfrecord(iter(self), _encoding, output_files, **kwargs)


class DatapipeForUnsupervisedSimCSE(Datapipe):
    """Datapipe for unsupervised SimCSE"""

    @classmethod
    def from_tfrecord_files(cls, input_files, **kwargs) -> tf.data.Dataset:
        dataset = utils.read_tfrecord_files(input_files, **kwargs)
        num_parallel_calls = kwargs.get("num_parallel_calls", utils.AUTOTUNE)
        buffer_size = kwargs.get("buffer_size", utils.AUTOTUNE)
        # parse example
        features = {
            "input_ids": tf.io.VarLenFeature(tf.int64),
            "segment_ids": tf.io.VarLenFeature(tf.int64),
            "attention_mask": tf.io.VarLenFeature(tf.int64),
        }
        dataset = dataset.map(
            lambda x: tf.io.parse_example(x, features),
            num_parallel_calls=num_parallel_calls,
        ).prefetch(buffer_size)
        dataset = dataset.map(
            lambda x: (
                tf.cast(tf.sparse.to_dense(x["input_ids"]), tf.int32),
                tf.cast(tf.sparse.to_dense(x["segment_ids"]), tf.int32),
                tf.cast(tf.sparse.to_dense(x["attention_mask"]), tf.int32),
            ),
            num_parallel_calls=num_parallel_calls,
        ).prefetch(buffer_size)
        # do transformation
        d = cls(**kwargs)
        return d(dataset, **kwargs)

    @classmethod
    def from_jsonl_files(cls, input_files, **kwargs) -> tf.data.Dataset:
        instances = readers.read_jsonl_files_for_unsup_simcse(input_files, **kwargs)
        return cls.from_instances(instances, **kwargs)

    @classmethod
    def from_instances(cls, instances, tokenizer: BertWordPieceTokenizer = None, vocab_file=None, **kwargs) -> tf.data.Dataset:
        parser = ParserForUnsupervisedSimCSE(tokenizer=tokenizer, vocab_file=vocab_file, **kwargs)
        examples = []
        for instance in instances:
            if not instance:
                continue
            e = parser.parse(instance, **kwargs)
            if not e:
                continue
            examples.append(e)
        return cls.from_examples(examples, **kwargs)

    @classmethod
    def from_dataset(cls, dataset: Dataset, **kwargs) -> tf.data.Dataset:
        examples = [e for _, e in enumerate(dataset) if e]
        return cls.from_examples(examples, **kwargs)

    @classmethod
    def from_examples(cls, examples: List[ExampleForUnsupervisedSimCSE], verbose=True, **kwargs) -> tf.data.Dataset:
        if not examples:
            logging.warning("examples is empty or null, skipped to build dataset.")
            return None
        if verbose:
            n = min(5, len(examples))
            for i in range(n):
                logging.info("Showing NO.%d example: %s", i, examples[i])

        def _to_dataset(x, dtype=tf.int32):
            x = tf.ragged.constant(x, dtype=dtype)
            d = tf.data.Dataset.from_tensor_slices(x)
            d = d.map(lambda x: x)
            return d

        dataset = tf.data.Dataset.zip(
            (
                _to_dataset([e.input_ids for e in examples], dtype=tf.int32),
                _to_dataset([e.segment_ids for e in examples], dtype=tf.int32),
                _to_dataset([e.attention_mask for e in examples], dtype=tf.int32),
            )
        )
        # do transformation
        d = cls(**kwargs)
        return d(dataset, **kwargs)

    def _filter(self, dataset: tf.data.Dataset, do_filter=True, max_sequence_length=512, **kwargs) -> tf.data.Dataset:
        if not do_filter:
            return dataset
        dataset = dataset.filter(lambda a, b, c: tf.size(a) <= max_sequence_length)
        return dataset

    def _to_dict(self, dataset: tf.data.Dataset, to_dict=True, **kwargs) -> tf.data.Dataset:
        num_parallel_calls = kwargs.get("num_parallel_calls", utils.AUTOTUNE)
        buffer_size = kwargs.get("buffer_size", utils.AUTOTUNE)
        if not to_dict:
            dataset = dataset.map(
                lambda a, b, c: ((a, b, c), None),
                num_parallel_calls=num_parallel_calls,
            ).prefetch(buffer_size)
            return dataset
        dataset = dataset.map(
            lambda x, y, z: ({"input_ids": x, "segment_ids": y, "attention_mask": z}, None),
            num_parallel_calls=num_parallel_calls,
        ).prefetch(buffer_size)
        return dataset

    def _fixed_padding(self, dataset: tf.data.Dataset, pad_id=0, max_sequence_length=512, **kwargs) -> tf.data.Dataset:
        pad_id = tf.constant(pad_id, dtype=tf.int32)
        maxlen = tf.constant(max_sequence_length, dtype=tf.int32)
        # fmt: off
        padded_shapes = ([maxlen, ], [maxlen, ], [maxlen, ])
        padding_values = (pad_id, pad_id, pad_id)
        # fmt: on
        return utils.batching_and_padding(dataset, padded_shapes=padded_shapes, padding_values=padding_values, **kwargs)

    def _batch_padding(self, dataset: tf.data.Dataset, pad_id=0, **kwargs) -> tf.data.Dataset:
        pad_id = tf.constant(pad_id, dtype=tf.int32)
        # fmt: off
        padded_shapes = ([None, ], [None, ], [None, ])
        padding_values = (pad_id, pad_id, pad_id)
        # fmt: on
        return utils.batching_and_padding(dataset, padded_shapes=padded_shapes, padding_values=padding_values, **kwargs)

    def _bucket_padding(self, dataset: tf.data.Dataset, pad_id=0, **kwargs) -> tf.data.Dataset:
        pad_id = tf.constant(pad_id, dtype=tf.int32)
        # fmt: off
        padded_shapes = ([None, ], [None, ], [None, ])
        padding_values = (pad_id, pad_id, pad_id)
        # fmt: on
        return utils.bucketing_and_padding(
            dataset,
            bucket_fn=lambda a, b, c: tf.size(a),
            padded_shapes=padded_shapes,
            padding_values=padding_values,
            **kwargs,
        )
