import numpy as np
import tensorflow as tf
#import cupy as cp
#we might not use np since the cpu computing is surprising large
#need to use tf or cp
import random
import gc

def clip_value(gradient, clip_norm=1):
  if clip_norm == 0:
    clip = False
  else:
    clip = True
  gnorm = np.linalg.norm(np.reshape(gradient,[-1]))
  cfactor = np.minimum(np.divide(clip_norm, gnorm) , 1)  if clip else 1
  return np.multiply(gradient, cfactor) if gnorm > clip_norm else gradient

"""
Server adam records 'v' and 'm' after aggregate
the problem is that each client may update on different direction

Client adam records 'vs' and 'ms' before aggregate
However, the model update on global direction

We decide not to use ADAM
"""

class Server:
  def __init__(self, model_factory, weight_delta_aggregator, clients_per_round, initialize = None):
    self._weight_delta_aggregator = weight_delta_aggregator
    self._clients_per_round = clients_per_round if clients_per_round == 'all' else int(clients_per_round)

    self.model = model_factory()
    if initialize is not None:
      self.model = initialize(self.model)

  def train(self, clients, val_x, val_y, test_x, test_y, start_round, num_of_rounds, expr_basename, clip: bool, history, history_delta_sum,
            x_chest: bool, optimizer, loss_fn, metrics, initial_lr, experiment_dir, 
            #last_deltas,
            progress_callback):
    self.model.compile(
        loss = loss_fn,
        metrics = metrics
    )

    server_weights = self.model.get_weights()

    for r in range(start_round, num_of_rounds):
      selected_clients = clients if self._clients_per_round == 'all' \
        else np.random.choice(clients, self._clients_per_round, replace=False)

      def cosine_decayed_learning_rate(initial_learning_rate, step, decay_steps = 1000, alpha = 0):
        step = min(step, decay_steps-1)
        cosine_decay = 0.5 * (1 + np.cos( np.pi * step / decay_steps))
        decayed = (1 - alpha) * cosine_decay + alpha
        return initial_learning_rate * decayed
      
      lr_decayed = cosine_decayed_learning_rate ( initial_learning_rate = initial_lr, step = r + 1)
        
      def Adam(lm, lv, d, lr_decayed, beta_1 = 0.9, beta_2 = 0.999, epsilon = 1e-7):
        g = np.multiply( lr_decayed, d) #since the delta return from clients including lr_decayed factor
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
      #this is for ADAM record, no matter server or client ADAM
      """
      deltas = []
      #moments = []
      #velocs = []
      #They are for clients ADAM. However, seems like we need to use the aggregate momentent and velocity 
      chkpt_path = experiment_dir/self._weight_delta_aggregator.__name__
      for i, client in enumerate(selected_clients):
        print(f'{expr_basename} round={r + 1}/{num_of_rounds}, client {i + 1}/{self._clients_per_round}',
              end='')
        delta = client.train(server_weights, lr_decayed, optimizer, loss_fn, metrics, val_x, val_y, x_chest, chkpt_path)
        #if r > 0:
        #  lms, lvs, delta = zip(*[ Adam(m, v, d, lr_decayed) for m, v, d in zip(last_m[i], last_v[i], delta)])
        #else: 
        #  lms, lvs, delta = zip(*[ Adam(0, 0, d, lr_decayed) for d in delta])
        #They are for clients ADAM. However, seems like we need to use the aggregate momentent and velocity 
        deltas.append ( delta )
        #moments.append ( lms )
        #velocs.append ( lvs )
        #They are for clients ADAM. However, seems like we need to use the aggregate momentent and velocity 

        if i != len(selected_clients) - 1:
          print('\r', end='')
        else:
          print('')

      #last_deltas = [moments, velocs]
      #this is for clients ADAM. However, seems like we need to use the aggregate momentent and velocity 
        
      if r==0:
        history_delta_sum = deltas
      else:
        history_delta_sum = [[np.add(h, d) for h, d in zip(hs, ds)] for hs, ds in zip(history_delta_sum, deltas)]
      
      """
      #server adam (clip gradient)
      for i, w in enumerate(server_weights):
        if 'gamma_mean' in self._weight_delta_aggregator.__name__:
          #aggr_delta = clip_value(self._weight_delta_aggregator([d[i] for d in deltas], 
          #              importance_weights, history_points = [np.divide(h[i], r + 1) for h in history_delta_sum]), lr_decayed)
          aggr_delta = self._weight_delta_aggregator([d[i] for d in deltas], 
                        importance_weights, history_points = [np.divide(h[i], r + 1) for h in history_delta_sum])
        else:
          #aggr_delta = clip_value(self._weight_delta_aggregator([d[i] for d in deltas], importance_weights), lr_decayed)
          aggr_delta = self._weight_delta_aggregator([d[i] for d in deltas], importance_weights)
        if r > 0:
          last_m[i], last_v[i], aggr_delta = Adam(last_m[i], last_v[i], aggr_delta, lr_decayed)
        else: 
          lm, lv, aggr_delta = Adam(0, 0, aggr_delta, lr_decayed)
          last_m.append(lm)
          last_v.append(lv)
        #server_weights[i] = w + np.multiply(lr_decayed, aggr_delta)
        server_weights[i] = w + aggr_delta
      last_deltas = [last_m, last_v]
      """
    
      #@TODO: Only need to update trainable parameters
      if 'record_gamma_mean_' in self._weight_delta_aggregator.__name__:
        if clip:
            server_weights = [w + clip_value(self._weight_delta_aggregator([d[i] for d in deltas], history_points = [np.divide(h[i], r + 1) for h in history_delta_sum]), lr_decayed)
                            for i, w in enumerate(server_weights)]
            #server_weights = [w + np.multiply(lr_decayed, clip_value(self._weight_delta_aggregator([d[i] for d in deltas], history_points = [np.divide(h[i], r + 1) for h in history_delta_sum])))
            #                for i, w in enumerate(server_weights)]
        else:
            server_weights = [w + self._weight_delta_aggregator([d[i] for d in deltas], history_points = [np.divide(h[i], r + 1) for h in history_delta_sum])
                            for i, w in enumerate(server_weights)]
      else:
        # todo change code below (to be nicer?):
        # aggregated_deltas = [self._weight_delta_aggregator(_, importance_weights) for _ in zip(*deltas)]
        # server_weights = [w + d for w, d in zip(server_weights, aggregated_deltas)]
        if clip:
            server_weights = [w + clip_value(self._weight_delta_aggregator([d[i] for d in deltas]), lr_decayed)
                              for i, w in enumerate(server_weights)]
            #server_weights = [w + np.multiply(lr_decayed, clip_value(self._weight_delta_aggregator([d[i] for d in deltas])))
            #                  for i, w in enumerate(server_weights)]
        else:
            server_weights = [w + self._weight_delta_aggregator([d[i] for d in deltas])
                              for i, w in enumerate(server_weights)]
      self.model.set_weights(server_weights)
      
      if x_chest:
        loss, acc, precision, recall = self.model.evaluate(test_x, test_y, verbose=0, batch_size = 16)
        print(f'{expr_basename} loss: {loss} - accuracy: {acc:.4%} - precision: {precision:.4%} - recall: {recall:.4%}')
      else:
        loss, acc = self.model.evaluate(test_x, test_y, verbose=0)
        print(f'{expr_basename} loss: {loss} - accuracy: {acc:.4%}')
        
      #history.append((loss, acc, val_loss, val_acc))
      history.append((loss, acc))
        
      if (r + 1) % (1 if x_chest else 10) == 0:
        progress_callback(history, server_weights, history_delta_sum)#, last_deltas)
      gc.collect()
