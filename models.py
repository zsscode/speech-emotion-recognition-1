import torch
import torch.nn as nn
from torch.nn import functional as F


def init_linear(m):
    if type(m) == nn.Linear:
        torch.nn.init.xavier_normal(m.weight)
        nn.init.constant(m.bias, 0)


class AttnDecoderRNN(nn.Module):
    """ Based on: https://medium.com/apache-mxnet/sentiment-analysis-via-self-attention-with-mxnet-gluon-dc774d38ba69 
    Also take a look at : https://github.com/gogyzzz/localatt_emorecog/blob/master/localatt/localatt.py"""
    def __init__(self, cfg):
        super(AttnDecoderRNN, self).__init__()
        self.do2 = nn.Dropout()

        self.blstm = torch.nn.LSTM(cfg.emb_dim, cfg.hidden_dim, num_layers=cfg.n_layers, bidirectional=cfg.bidirectional)

        self.d_dim = 64
        self.r_dim = 32

        self.fc1 = nn.Linear(cfg.hidden_dim, self.d_dim)
        self.fc2 = nn.Linear(self.d_dim, self.r_dim)
        self.fc3 = nn.Linear(self.r_dim, cfg.num_classes)

        self.apply(init_linear)

    def forward(self, input):
        """ input = [seq_len, batch size, emb_dim] """
        input = torch.Tensor(input)

        """ output = [seq_len, batch size, hidden_dim] """
        output, (hidden, cell) = self.blstm(input)

        """ output_trans = [seq_len, batch size, d_dim] """
        output_trans = self.fc1(output)
        output_trans = F.tanh(output_trans)

        """ output_scores = [seq_len, batch size, r_dim] """
        output_scores = self.fc2(output_trans)
        attention = F.softmax(output_scores)

        """ output_trans2_sum = [batch size, r_dim] """
        outputs_scaled = torch.sum(attention, dim=0)

        """ output_logits = [batch size, num_classes] """
        output_logits = self.fc3(outputs_scaled)

        return output_logits


class RNN(torch.nn.Module):
    def __init__(self, cfg):
        super().__init__()
        fc_size = cfg.hidden_dim * 2 if cfg.bidirectional else cfg.hidden_dim
        self.lstm = torch.nn.LSTM(cfg.emb_dim, cfg.hidden_dim, num_layers=cfg.n_layers, bidirectional=cfg.bidirectional)

        self.fc = torch.nn.Linear(fc_size, cfg.num_classes)

        self.dropout = torch.nn.Dropout(cfg.dropout)
        self.dropout2 = torch.nn.Dropout(cfg.dropout2)

        """ Xavier Initialization """
        for layer_p in self.lstm._all_weights:
            for p in layer_p:
                if 'weight' in p:
                    torch.nn.init.xavier_uniform_(self.lstm.__getattr__(p))

        torch.nn.init.xavier_uniform_(self.fc.weight)

    def forward(self, x):
        """ x = [sent len, batch size, emb dim] """
        x = torch.Tensor(x)
        x = self.dropout(x)

        """ output      = [sent len, batch size, hid dim * num directions]
            hidden&cell = [num layers * num directions, batch size, hid dim] """
        output, (hidden, cell) = self.lstm(x)

        """ concat the final forward (hidden[-2,:,:]) and backward (hidden[-1,:,:]) hidden layers and apply dropout """
        if self.lstm.bidirectional:
            hidden = self.dropout2(torch.cat((hidden[-2, :, :], hidden[-1, :, :]), dim=1))
        else:
            hidden = self.dropout2(hidden[-1, :, :])
        hidden = hidden.squeeze(0).float()
        return self.fc(hidden)


class AttentionModel(torch.nn.Module):
    """Taken from https://github.com/prakashpandey9/Text-Classification-Pytorch/blob/master/models/LSTM_Attn.py"""
    def __init__(self, cfg):
        super(AttentionModel, self).__init__()

        """
        Arguments
        ---------
        batch_size : Size of the batch which is same as the batch_size of the data returned by the TorchText BucketIterator
        output_size : 2 = (pos, neg)
        hidden_sie : Size of the hidden_state of the LSTM
        vocab_size : Size of the vocabulary containing unique words
        embedding_length : Embeddding dimension of GloVe word embeddings
        weights : Pre-trained GloVe word_embeddings which we will use to create our word_embedding look-up table 

        --------
        """
        self.batch_size = cfg.batch_size
        self.output_size = cfg.num_classes
        self.hidden_size = cfg.hidden_dim
        self.embedding_length = cfg.emb_dim

        self.dropout = torch.nn.Dropout(cfg.dropout)
        self.dropout2 = torch.nn.Dropout(cfg.dropout2)

        self.lstm = nn.LSTM(self.embedding_length, self.hidden_size)
        self.label = nn.Linear(self.hidden_size, self.output_size)

    def attention_net(self, lstm_output, final_state):

        """
        Now we will incorporate Attention mechanism in our LSTM model. In this new model, we will use attention to compute soft alignment score corresponding
        between each of the hidden_state and the last hidden_state of the LSTM. We will be using torch.bmm for the batch matrix multiplication.

        Arguments
        ---------

        lstm_output : Final output of the LSTM which contains hidden layer outputs for each sequence.
        final_state : Final time-step hidden state (h_n) of the LSTM

        ---------

        Returns : It performs attention mechanism by first computing weights for each of the sequence present in lstm_output and and then finally computing the
                  new hidden state.

        Tensor Size :
                    hidden.size() = (batch_size, hidden_size)
                    attn_weights.size() = (batch_size, num_seq)
                    soft_attn_weights.size() = (batch_size, num_seq)
                    new_hidden_state.size() = (batch_size, hidden_size)

        """

        hidden = final_state.squeeze(0)
        attn_weights = torch.bmm(lstm_output, hidden.unsqueeze(2)).squeeze(2)
        soft_attn_weights = F.softmax(attn_weights, 1)
        new_hidden_state = torch.bmm(lstm_output.transpose(1, 2), soft_attn_weights.unsqueeze(2)).squeeze(2)

        return new_hidden_state

    def extract(self, input):
        input = input.swapaxes(0, 1)
        input = torch.Tensor(input)
        input = self.dropout(input)

        output, (final_hidden_state, final_cell_state) = self.lstm(
            input)  # , ( h_0, c_0))  # final_hidden_state.size() = (1, batch_size, hidden_size)
        output = output.permute(1, 0, 2)  # output.size() = (batch_size, num_seq, hidden_size)

        attn_output = self.attention_net(output, final_hidden_state)
        return attn_output

    def classify(self, attn_output):
        attn_output = self.dropout2(attn_output)
        logits = self.label(attn_output)
        return logits.squeeze(1)

    def forward(self, input, batch_size=None):

        """
        Parameters
        ----------
        input_sentence: input_sentence of shape = (batch_size, num_sequences)
        batch_size : default = None. Used only for prediction on a single sentence after training (batch_size = 1)

        Returns
        -------
        Output of the linear layer containing logits for pos & neg class which receives its input as the new_hidden_state which is basically the output of the Attention network.
        final_output.shape = (batch_size, output_size)

        """
        # TODO CHECK THIS h_0 and c_0 reset
        # if batch_size is None:
        #     h_0 = Variable(torch.zeros(1, self.batch_size, self.hidden_size).cuda())
        #     c_0 = Variable(torch.zeros(1, self.batch_size, self.hidden_size).cuda())
        # else:
        #     h_0 = Variable(torch.zeros(1, batch_size, self.hidden_size).cuda())
        #     c_0 = Variable(torch.zeros(1, batch_size, self.hidden_size).cuda())

        attn_output = self.extract(input)
        logits = self.classify(attn_output)
        return logits


class CNN(nn.Module):
    def __init__(self, cfg):
        super(CNN, self).__init__()
        self.conv1 = nn.Conv2d(1, cfg.num_filters[0], cfg.conv_size, padding=1)
        self.conv2 = nn.Conv2d(cfg.num_filters[0], cfg.num_filters[1], cfg.conv_size, padding=1)
        self.conv3 = nn.Conv2d(cfg.num_filters[1], cfg.num_filters[2], cfg.conv_size, padding=1)
        self.conv4 = nn.Conv2d(cfg.num_filters[2], cfg.num_filters[3], cfg.conv_size, padding=1)
        self.pool = nn.MaxPool2d(cfg.pool_size, cfg.pool_size)

        self.flat_size = cfg.num_filters[3] * 4 * 4
        self.fc2 = nn.Linear(self.flat_size, cfg.num_classes)
        self.dropout = torch.nn.Dropout(cfg.dropout)

    def extract(self, x):
        x = torch.Tensor(x)
        x = x.unsqueeze(1)
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = self.pool(F.relu(self.conv3(x)))
        x = self.pool(F.relu(self.conv4(x)))
        x = x.view(-1, self.flat_size)
        return x

    def classify(self, x):
        x = self.dropout(x)
        x = self.fc2(x)
        return x

    def forward(self, x):
        x = self.extract(x)
        x = self.classify(x)
        return x


class EnsembleModel(nn.Module):
    def __init__(self, acoustic_model, linguistic_model):
        super(EnsembleModel, self).__init__()
        self.acoustic_model = acoustic_model
        self.linguistic_model =linguistic_model
        self.feature_size = self.linguistic_model.hidden_size + self.acoustic_model.flat_size
        self.fc = nn.Linear(self.feature_size, 4)
        self.dropout = torch.nn.Dropout(0.5)

    def forward(self, acoustic_features, linguistic_features):
        acoustic_output_features = self.acoustic_model.extract(acoustic_features)
        linguistic_output_features = self.linguistic_model.extract(linguistic_features)
        all_features = torch.cat((acoustic_output_features, linguistic_output_features), 1)
        return self.fc(self.dropout(all_features))


class Block(nn.Module):
    '''Depthwise conv + Pointwise conv'''
    def __init__(self, in_planes, out_planes, stride=1):
        super(Block, self).__init__()
        self.conv1 = nn.Conv2d(in_planes, in_planes, kernel_size=3, stride=stride, padding=1, groups=in_planes, bias=False)
        self.bn1 = nn.BatchNorm2d(in_planes)
        self.conv2 = nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=1, padding=0, bias=False)
        self.bn2 = nn.BatchNorm2d(out_planes)

    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = F.relu(self.bn2(self.conv2(out)))
        return out


class MobileNet(nn.Module):
    # (128,2) means conv planes=128, conv stride=2, by default conv stride=1
    # cfg = [64, (128,2), 128, (256,2), 256, (512,2), 512, 512, 512, 512, 512, (1024,2), 1024]
    num_filters = 16
    cfg = [(num_filters, 2), (num_filters, 2)]

    def __init__(self, cfg, num_classes=4):
        super(MobileNet, self).__init__()
        self.conv1 = nn.Conv2d(1, 16, kernel_size=3, stride=2, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(16)
        self.layers = self._make_layers(in_planes=16)
        self.linear = nn.Linear(256, num_classes)
        self.dropout = torch.nn.Dropout(0.5)

    def _make_layers(self, in_planes):
        layers = []
        for x in self.cfg:
            out_planes = x if isinstance(x, int) else x[0]
            stride = 1 if isinstance(x, int) else x[1]
            layers.append(Block(in_planes, out_planes, stride))
            in_planes = out_planes
        return nn.Sequential(*layers)

    def forward(self, x):
        x = torch.Tensor(x)
        x = x.unsqueeze(1)
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.layers(out)
        out = F.avg_pool2d(out, 2)
        out = out.view(out.size(0), -1)
        out = self.dropout(out)
        out = self.linear(out)
        return out