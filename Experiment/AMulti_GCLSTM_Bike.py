import os
import numpy as np

from local_path import data_dir, tf_model_dir
from Model.AMulti_GCLSTM import AMulti_GCLSTM
from Train.EarlyStopping import EarlyStoppingTTest
from Train.MiniBatchTrain import MiniBatchTrainMultiData
from EvalClass.Accuracy import Accuracy
from Utils.json_api import saveJson
from Experiment.data_loader import gcn_data_loader


def get_md5(string):
    import hashlib
    m = hashlib.md5()
    m.update(string)
    return m.hexdigest()

def parameter_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Argument Parser")
    # data source
    parser.add_argument('--City', default='DC')
    # network parameter
    parser.add_argument('--T', default='6')
    parser.add_argument('--K', default='1')
    parser.add_argument('--L', default='1')
    parser.add_argument('--Graph', default='Distance')
    parser.add_argument('--GLL', default='1')
    parser.add_argument('--LSTMUnits', default='64')
    parser.add_argument('--GALUnits', default='64')
    parser.add_argument('--GALHeads', default='2')
    parser.add_argument('--DenseUnits', default='32')
    # Training data parameters
    parser.add_argument('--TrainDays', default='All')
    # Graph parameter
    parser.add_argument('--TC', default='0')
    parser.add_argument('--TD', default='1000')
    parser.add_argument('--TI', default='500')
    # training parameters
    parser.add_argument('--Epoch', default='5000')
    parser.add_argument('--Train', default='True')
    parser.add_argument('--lr', default='1e-3')
    parser.add_argument('--patience', default='20')
    parser.add_argument('--BatchSize', default='64')
    # device parameter
    parser.add_argument('--Device', default='1')
    # version contral
    parser.add_argument('--CodeVersion', default='Debug0222')
    return parser


parser = parameter_parser()
args = parser.parse_args()

code_parameters = vars(args)
train = True if code_parameters.pop('Train') == 'True' else False
GPU_DEVICE = code_parameters.pop('Device')
code_version_md5 = get_md5(str(sorted(code_parameters.items(), key=lambda x:x[0], reverse=False)).encode())
 
for key, value in sorted(code_parameters.items(), key=lambda x:x[0], reverse=False):
    print(key, value)
saveJson(code_parameters, os.path.join(tf_model_dir, 'Config_{}.json'.format(code_version_md5)))

# Config data loader
data_loader = gcn_data_loader(args)

# parse parameters
K = [int(e) for e in args.K.split(',') if len(e) > 0]
L = [int(e) for e in args.L.split(',') if len(e) > 0]
lr = float(args.lr)
patience = int(args.patience)
num_epoch = int(args.Epoch)
batch_size = int(args.BatchSize)

code_version = 'AMulti_GCLSTM_{}'.format(code_version_md5)

AMulti_GCLSTM_Obj = AMulti_GCLSTM(num_node=data_loader.station_number,
                                  GCN_K=K, GCN_layers=L,
                                  num_graph=data_loader.LM.shape[0],
                                  external_dim=data_loader.external_dim,
                                  GCLSTM_layers=int(args.GLL),
                                  gal_units=int(args.GALUnits), gal_num_heads=int(args.GALHeads),
                                  T=int(args.T), num_filter_conv1x1=int(args.DenseUnits),
                                  num_hidden_units=int(args.LSTMUnits), lr=lr,
                                  code_version=code_version, GPU_DEVICE=GPU_DEVICE, model_dir=tf_model_dir)

AMulti_GCLSTM_Obj.build()

de_normalizer = None

if train:

    try:
        AMulti_GCLSTM_Obj.load(code_version)
    except Exception as e:
        print('No model found, start training!')

    val_rmse_record = []
    test_rmse_record = []

    early_stop = EarlyStoppingTTest(length=patience, p_value_threshold=0.1)

    best_record = None

    for epoch in range(num_epoch):

        mini_batch_train = MiniBatchTrainMultiData([data_loader.train_x, data_loader.train_y, data_loader.train_ef],
                                                   batch_size=batch_size)

        loss_list = []

        for i in range(mini_batch_train.num_batch):
            X, y, ef = mini_batch_train.get_batch()

            l = AMulti_GCLSTM_Obj.fit(
                {'input': X,
                 'target': y,
                 'laplace_matrix': data_loader.LM,
                 'external_input': ef},
                global_step=epoch, summary=False)['loss']

            loss_list.append(l)

        # validation
        val_rmse, = AMulti_GCLSTM_Obj.evaluate(
            {
                'input': data_loader.val_x,
                'target': data_loader.val_y,
                'laplace_matrix': data_loader.LM,
                'external_input': data_loader.val_ef
            }, cache_volume=64, sequence_length=len(data_loader.val_x),
            target_key='target', prediction_key='prediction', metric=[Accuracy.RMSE], threshold=0,
        )

        # test
        test_rmse, = AMulti_GCLSTM_Obj.evaluate(
            {
                'input': data_loader.test_x,
                'target': data_loader.test_y,
                'laplace_matrix': data_loader.LM,
                'external_input': data_loader.test_ef
            }, cache_volume=32, sequence_length=len(data_loader.test_x),
            target_key='target', prediction_key='prediction', metric=[Accuracy.RMSE], threshold=0,
        )

        val_rmse_record.append([float(val_rmse)])
        test_rmse_record.append([float(test_rmse)])

        if early_stop.stop(val_rmse):
            break

        if best_record is None or val_rmse < best_record:
            best_record = val_rmse
            AMulti_GCLSTM_Obj.save(code_version)

        AMulti_GCLSTM_Obj.manual_summary(epoch)

        AMulti_GCLSTM_Obj.add_summary(name='train_loss', value=np.mean(loss_list), global_step=epoch)
        AMulti_GCLSTM_Obj.add_summary(name='val_rmse', value=val_rmse, global_step=epoch)
        AMulti_GCLSTM_Obj.add_summary(name='test_rmse', value=test_rmse, global_step=epoch)

        print(code_version, epoch,
              'train_loss %.5f' % np.mean(loss_list), 'val_rmse %.5f' % val_rmse, 'test_rmse %.5f' % test_rmse)

AMulti_GCLSTM_Obj.load(code_version)

# test
test_rmse, = AMulti_GCLSTM_Obj.evaluate(
            {
                'input': data_loader.test_x,
                'target': data_loader.test_y,
                'laplace_matrix': data_loader.LM,
                'external_input': data_loader.test_ef
            }, cache_volume=64, sequence_length=len(data_loader.test_x),
            target_key='target', prediction_key='prediction', metric=[Accuracy.RMSE], threshold=0)

print('########################################################################')
print(code_version, 'Test RMSE', test_rmse)
print('########################################################################')

AMulti_GCLSTM_Obj.close()