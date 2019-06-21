import numpy as np
import tensorflow as tf

from ..model_unit import GAL, GCL, BaseModel


class GACN(BaseModel):
    def __init__(self,
                 num_node,
                 gcl_k,
                 gcl_layers,
                 gal_num_heads,
                 gal_layers,
                 gal_units,
                 dense_units,
                 time_embedding_dim,
                 external_feature_dim,
                 input_dim,
                 T,
                 lr,
                 code_version,
                 model_dir,
                 GPU_DEVICE='0'):
        
        super(GACN, self).__init__(code_version=code_version, model_dir=model_dir, GPU_DEVICE=GPU_DEVICE)

        self._num_node = num_node

        self._input_dim = input_dim

        self._gal_num_heads = gal_num_heads
        self._gal_layers = gal_layers
        self._gal_units = gal_units
        self._time_embedding_dim = time_embedding_dim
        self._external_feature_dim = external_feature_dim

        self._gcl_k = gcl_k
        self._gcl_layers = gcl_layers

        self._dense_units = dense_units

        self._T = T
        self._lr = lr

    def build(self):
        with self._graph.as_default():
            # Input
            input_series = tf.placeholder(tf.float32, [None, self._T, None, self._input_dim], name='input_series')

            input = tf.transpose(input_series, perm=[0, 2, 1, 3])

            target = tf.placeholder(tf.float32, [None, None, 1], name='target')
            laplace_matrix = tf.placeholder(tf.float32, [None, None], name='laplace_matrix')

            # recode input
            self._input['input_series'] = input_series.name
            self._input['target'] = target.name
            self._input['laplace_matrix'] = laplace_matrix.name

            if self._time_embedding_dim and self._time_embedding_dim > 0:
                time_embedding = tf.placeholder(tf.float32, [self._T, self._time_embedding_dim], name='time_embedding')
                # recode input
                self._input['time_embedding'] = time_embedding.name
                time_embedding = tf.reshape(time_embedding, [1, 1, self._T, self._time_embedding_dim])
                time_embedding = tf.tile(time_embedding,
                                         [tf.shape(input)[0], tf.shape(input)[1], 1, 1])
                input = tf.concat((input, time_embedding), axis=-1)
                attention_input = tf.reshape(input, [-1, self._T, self._input_dim + self._time_embedding_dim])
            else:
                attention_input = tf.reshape(input, [-1, self._T, self._input_dim])

            attention_output_list = []
            for loop_index in range(self._gal_layers):
                with tf.variable_scope('res_gal_%s' % loop_index, reuse=False):
                    attention_input = GAL.add_residual_ga_layer(attention_input,
                                                                num_head=self._gal_num_heads,
                                                                units=self._gal_units)
                    attention_output_list.append(attention_input)

            attention_output = tf.reshape(attention_output_list[-1],
                                          [tf.shape(input)[0], tf.shape(input)[1],
                                           self._T, attention_input.get_shape()[-1]])

            # GCN
            gcn_input_feature = tf.reduce_mean(attention_output, axis=-2)

            gcn_output = GCL.add_gc_layer(gcn_input_feature, self._gcl_k, laplace_matrix)

            if self._external_feature_dim is not None and self._external_feature_dim > 0:
                external_input = tf.placeholder(tf.float32, [None, self._external_feature_dim], name='external_input')
                self._input['external_input'] = external_input.name

                external_dense = tf.layers.dense(inputs=external_input, units=10)
                external_dense = tf.tile(tf.reshape(external_dense, [-1, 1, 10]),
                                         [1, tf.shape(gcn_output)[-2], 1])
                gcn_output = tf.concat([gcn_output, external_dense], axis=-1)

            middle_output = tf.keras.layers.Dense(units=self._dense_units)(gcn_output)
            prediction = tf.keras.layers.Dense(units=1)(middle_output)

            loss_pre = tf.sqrt(tf.reduce_mean(tf.square(target - prediction)), name='loss')
            train_operation = tf.train.AdamOptimizer(self._lr).minimize(loss_pre, name='train_op')

            # record output
            self._output['prediction'] = prediction.name
            self._output['loss'] = loss_pre.name

            # record train operation
            self._op['train_op'] = train_operation.name

            self._saver = tf.train.Saver(max_to_keep=None)
            self._variable_init = tf.global_variables_initializer()

            self.trainable_vars = np.sum([np.prod(v.get_shape().as_list()) for v in tf.trainable_variables()])

            print('Trainable Variables', self.trainable_vars)

            # Add summary
            self._summary = self._summary_histogram().name

        self._session.run(self._variable_init)

    # Step 1 : Define your '_get_feed_dict function‘, map your input to the tf-model
    def _get_feed_dict(self, input, laplace_matrix, target=None, external_input=None, time_embedding=None):
        feed_dict = {
            'input_series': input,
            'laplace_matrix': laplace_matrix,
        }
        if target is not None:
            feed_dict['target'] = target
        if self._time_embedding_dim is not None and self._time_embedding_dim > 0:
            feed_dict['time_embedding'] = time_embedding
        if self._external_feature_dim is not None and self._external_feature_dim > 0:
            feed_dict['external_input'] = external_input

        return feed_dict

    # Step 2 : build the fit function using BaseModel._fit
    def fit(self,
            input,
            laplace_matrix,
            target,
            time_embedding=None,
            external_input=None,
            batch_size=64, max_epoch=10000,
            validate_ratio=0.1,
            early_stop_method='t-test',
            early_stop_length=50,
            early_stop_patience=0.1):

        evaluate_loss_name = 'loss'

        feed_dict = self._get_feed_dict(input=input, laplace_matrix=laplace_matrix, target=target,
                                        external_input=external_input, time_embedding=time_embedding)

        return self._fit(feed_dict=feed_dict,
                         sequence_index='input_series',
                         output_names=[evaluate_loss_name],
                         evaluate_loss_name=evaluate_loss_name,
                         op_names=['train_op'],
                         batch_size=batch_size,
                         max_epoch=max_epoch,
                         validate_ratio=validate_ratio,
                         early_stop_method=early_stop_method,
                         early_stop_length=early_stop_length,
                         early_stop_patience=early_stop_patience)

    def predict(self, input, laplace_matrix, time_embedding=None, external_input=None, cache_volume=64):
        
        feed_dict = self._get_feed_dict(input=input, laplace_matrix=laplace_matrix,
                                        external_input=external_input, time_embedding=time_embedding)

        output = self._predict(feed_dict=feed_dict, output_names=['prediction'], sequence_length=len(input),
                               cache_volume=cache_volume)

        return output['prediction']

    def evaluate(self, input, laplace_matrix, target, metrics,
                 time_embedding=None, external_input=None, cache_volume=64, **kwargs):

        prediction = self.predict(input, laplace_matrix, time_embedding=time_embedding,
                                  external_input=external_input, cache_volume=cache_volume)

        return [e(prediction=prediction, target=target, **kwargs) for e in metrics]