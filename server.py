import numpy as np
import tensorflow as tf
import random

class Server:
  def __init__(self, model_factory, clients_importance_preprocess, weight_delta_aggregator, clients_per_round):
    self._clients_importance_preprocess = clients_importance_preprocess
    self._weight_delta_aggregator = weight_delta_aggregator
    self._clients_per_round = clients_per_round if clients_per_round == 'all' else int(clients_per_round)

    self.model = model_factory()

    self.model.compile(
      loss=tf.keras.losses.SparseCategoricalCrossentropy(),
      metrics=['accuracy']
    )

  def train(self,seed, clients, test_x, test_y, start_round, num_of_rounds, expr_basename, history, history_delta_sum, #last_deltas,
            progress_callback):
    client2importance = self._clients_importance_preprocess([c.num_of_samples for c in clients])

    server_weights = self.model.get_weights()

    for r in range(start_round, num_of_rounds):
      selected_clients = clients if self._clients_per_round == 'all' \
        else np.random.choice(clients, self._clients_per_round, replace=False)
      
      np.random.seed(seed+r)
      tf.random.set_seed(seed+r)
      random.seed(seed+r)

      def decayed_learning_rate(initial_learning_rate, step, decay_steps = 1000, alpha = 0):
        step = min(step, decay_steps)
        cosine_decay = 0.5 * (1 + np.cos( np.pi * step / decay_steps))
        decayed = (1 - alpha) * cosine_decay + alpha
        return initial_learning_rate * decayed
      lr_decayed = decayed_learning_rate ( initial_learning_rate = 5e-2, step = r + 1)

      def Adam(lm, lv, d, lr_decayed, beta_1 = 0.9, beta_2 = 0.999, epsilon = 1e-7):
        g = np.multiply( lr_decayed, d)
        m = np.divide(np.multiply( beta_1, lm) + np.multiply( 1-beta_1, g), 1 - np.power(beta_1, r+1) ) 
        v = np.divide(np.multiply( beta_2, lv) + np.multiply( 1-beta_2, np.square(g)), 1 - np.power(beta_2, r+1))  
        d = np.divide( np.multiply( lr_decayed, m),  np.add( np.sqrt(v), epsilon) )
        return m, v, d
      """
      if r>0:
        last_m = last_deltas[0]
        last_v = last_deltas[1]
      else:
        last_m = []
        last_v = []
      """
      deltas = []
      #moments = []
      #velocs = []
      #seems like we need to use the aggregate momentent and velocity 
      for i, client in enumerate(selected_clients):
        print(f'{expr_basename} round={r + 1}/{num_of_rounds}, client {i + 1}/{self._clients_per_round}',
              end='')
        delta = client.train(server_weights, lr_decayed)
        #if r > 0:
        #  lms, lvs, delta = zip(*[ Adam(m, v, d, lr_decayed) for m, v, d in zip(last_m[i], last_v[i], delta)])
        #else: 
        #  lms, lvs, delta = zip(*[ Adam(0, 0, d, lr_decayed) for d in delta])
        deltas.append ( delta )
        #moments.append ( lms )
        #velocs.append ( lvs )

        if i != len(selected_clients) - 1:
          print('\r', end='')
        else:
          print('')

      #last_deltas = [moments, velocs]

      if client2importance is not None:
        importance_weights = [client2importance[c.idx] for c in selected_clients]
      else:
        importance_weights = None
        
      if r==0:
        history_delta_sum = deltas
      else:
        history_delta_sum = [[np.add(h, d) for h, d in zip(hs, ds)] for hs, ds in zip(history_delta_sum, deltas)]
      
      """
      for i, w in enumerate(server_weights):
        if 'gamma_mean' in self._weight_delta_aggregator.__name__:
          aggr_delta = self._weight_delta_aggregator([d[i] for d in deltas], 
                        importance_weights, history_points = [np.divide(h[i], r + 1) for h in history_delta_sum])
        else:
          aggr_delta = self._weight_delta_aggregator([d[i] for d in deltas], importance_weights, )
        if r > 0:
          last_m[i], last_v[i], aggr_delta = Adam(last_m[i], last_v[i], aggr_delta, lr_decayed)
        else: 
          lm, lv, aggr_delta = Adam(0, 0, aggr_delta, lr_decayed)
          last_m.append(lm)
          last_v.append(lv)
        server_weights[i] = w + aggr_delta
      last_deltas = [last_m, last_v]
      """
      if 'gamma_mean' in self._weight_delta_aggregator.__name__:
        server_weights = [w + self._weight_delta_aggregator([d[i] for d in deltas], importance_weights, history_points = [np.divide(h[i], r + 1) for h in history_delta_sum])
                        for i, w in enumerate(server_weights)]
      else:
        # todo change code below (to be nicer?):
        # aggregated_deltas = [self._weight_delta_aggregator(_, importance_weights) for _ in zip(*deltas)]
        # server_weights = [w + d for w, d in zip(server_weights, aggregated_deltas)]
        server_weights = [w + self._weight_delta_aggregator([d[i] for d in deltas], importance_weights)
                          for i, w in enumerate(server_weights)]
      
      self.model.set_weights(server_weights)
      loss, acc = self.model.evaluate(test_x, test_y, verbose=0)
      print(f'{expr_basename} loss: {loss} - accuracy: {acc:.2%}')
      history.append((loss, acc))
      if (r + 1) % 10 == 0:
        progress_callback(history, server_weights, history_delta_sum)#, last_deltas)